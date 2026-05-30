"""
Core image processing pipeline.

This module holds the single source of truth for turning a source photo into a
bordered (optionally exif + palette) output. It is deliberately GUI-agnostic and
picklable-friendly so the same `process_image` can be called both:

  * sequentially from the GUI thread (with a progress callback for per-stage UI), and
  * inside a ProcessPoolExecutor worker (with progress_cb=None) for parallel folder runs.
"""
import os
import logging
from PIL import Image
from exif import get_exif
from palette import load_image_color_palette, overlay_palette
from border import BorderType, create_border, draw_border, draw_exif
from text import validate_font

# Wide aspect ratios on large source images legitimately produce very large
# canvases (e.g. a 33MP portrait padded to 16:9 exceeds 100 megapixels). Pillow's
# default ~89MP guard treats these as possible decompression-bomb attacks and can
# raise, killing the file mid-batch. We are creating these canvases deliberately
# from the user's own files, so the guard is a false positive here. Disable it.
Image.MAX_IMAGE_PIXELS = None

logger = logging.getLogger(__name__)

# Pipeline stage labels, in order. Exposed so the GUI can render a stage list.
STAGES = ("open", "border", "exif", "palette", "save")

FILETYPES = ("jpg", "jpeg", "png")


def _noop(stage: str, fraction: float) -> None:
    """Default progress callback: does nothing.

    Used when no progress reporting is wanted (e.g. inside parallel workers,
    where callbacks cannot cross the process boundary anyway)."""
    return None


def resolve_output_path(src_path: str, input_root: str, output_root: str, save_as_name: str, ext: str,
                        overwrite: bool = True) -> str:
    """Compute the output path inside output_root, mirroring the source's
    location relative to input_root so that identically-named files in
    different sub-directories never collide.

    Args:
        src_path: Full path to the source image.
        input_root: The root folder the batch was scanned from (or the file's
                    own directory for single-file runs).
        output_root: The chosen output folder.
        save_as_name: The computed output basename (without extension).
        ext: File extension (without dot).
        overwrite: If True (default), return the natural path even if it exists
                   (it will be overwritten on save). If False, and the natural
                   path already exists, append " (1)", " (2)", ... until a free
                   name is found, so previous outputs are never clobbered.

    Returns:
        Full destination path. Parent directories are created.
    """
    src_dir = os.path.dirname(os.path.abspath(src_path))
    input_root = os.path.abspath(input_root)

    # Mirror the relative sub-path of the source dir under the output root.
    try:
        rel_dir = os.path.relpath(src_dir, input_root)
    except ValueError:
        # Different drive on Windows etc. - fall back to flat.
        rel_dir = ""
    if rel_dir == os.curdir or rel_dir.startswith(".."):
        rel_dir = ""

    dest_dir = os.path.join(output_root, rel_dir) if rel_dir else output_root
    os.makedirs(dest_dir, exist_ok=True)

    candidate = os.path.join(dest_dir, f"{save_as_name}.{ext}")
    if overwrite or not os.path.exists(candidate):
        return candidate

    # Don't overwrite: find the next free " (n)" suffix.
    n = 1
    while True:
        candidate = os.path.join(dest_dir, f"{save_as_name} ({n}).{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def process_image(path: str,
                  add_exif: bool,
                  add_palette: bool,
                  border_type: BorderType,
                  font: tuple,
                  boldfont: tuple,
                  fontdir: str,
                  output_root: str,
                  input_root: str = None,
                  progress_cb=None,
                  preview_max_edge: int = None,
                  target_ratio: float = None,
                  overwrite: bool = True) -> str:
    """Add a border to an image and save it into output_root.

    Supported image types: jpg, jpeg, png.

    Args:
        path: The image file path.
        add_exif: Add photo exif information to the border.
        add_palette: Add colour palette information to the border.
        border_type: The type of border to add.
        font: (fontFileName, fontVariantIndex).
        boldfont: (fontFileName, fontVariantIndex).
        fontdir: Directory containing the font files.
        output_root: Folder to write the output into.
        input_root: Root folder the scan started from, used to mirror sub-folder
                    structure in the output. Defaults to the file's own directory.
        progress_cb: Optional callable(stage_name: str, fraction: float). Called
                     as each stage completes. None disables reporting (used by
                     parallel workers). fraction is 0..1 across the whole file.
        preview_max_edge: If set, the SOURCE image is downscaled so its longest
                          edge is at most this many pixels BEFORE processing.
                          Used only for previews. Note: because border and font
                          sizes are derived from absolute pixel dimensions, a
                          preview produced this way is proportionally accurate
                          only if the same downscale is applied consistently -
                          which it is, since the whole pipeline runs on the
                          downscaled copy. See GUI for how this is used.
        target_ratio: Optional final canvas width/height ratio to pad to (adds
                      border on the deficient axis, never crops). None = native.

    Returns:
        The output path, or None if the file type was unsupported.
    """
    cb = progress_cb or _noop

    filetypes = list(FILETYPES)
    path_dot_parts = path.split('.')
    ext = path_dot_parts[-1:][0]
    filename = os.path.basename(".".join(path_dot_parts[:-1]))

    if not ext or ext.lower() not in filetypes:
        logger.error(f'Image must be one of {filetypes}')
        return None

    if input_root is None:
        input_root = os.path.dirname(os.path.abspath(path))

    # --- open -------------------------------------------------------------
    img = Image.open(path)
    # EXIF must be read from the original (full-res) image before any resize,
    # and the orientation/metadata is independent of pixel size.
    exif = get_exif(img) if add_exif else None

    if preview_max_edge:
        longest = max(img.width, img.height)
        if longest > preview_max_edge:
            scale = preview_max_edge / longest
            resized = img.resize(
                (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                Image.BILINEAR,
            )
            img.close()
            img = resized
    cb("open", 0.15)

    # --- border -----------------------------------------------------------
    border = create_border(img.width, img.height, border_type, target_ratio=target_ratio)
    img_with_border = draw_border(img, border)
    save_as = f'{filename}_border-{border.border_type}'
    cb("border", 0.35)

    # --- palette (compute early so EXIF text can avoid it) ----------------
    # The palette image is rendered first (but pasted last) so we know its
    # footprint before sizing the EXIF text. In the polaroid layout the text is
    # left-aligned and the palette sits bottom-right, so the text must be sized to
    # fit the space to the LEFT of the palette or it collides (which it did on
    # real EXIF strings). For centered border types the text is centered and this
    # constraint does not apply.
    color_palette = None
    palette_x = palette_y = 0
    available_text_width = None
    if add_palette:
        palette_size = round(border.bottom / 3)
        color_palette = load_image_color_palette(img, palette_size)
        margin = round(palette_size / 2)
        # Anchor the palette to the bottom-right corner of the PHOTO, not the
        # canvas. The image's right edge sits at border.left + img.width. Anchoring
        # here means the palette always tucks under the photo's right side and
        # never drifts toward the centre as the side borders grow on wide aspect
        # ratios (previously it was anchored to border.right, which pushed it
        # inward by the full border width - up to thousands of px on 16:9).
        image_right_edge = border.left + img.width
        palette_x = image_right_edge - color_palette.width - margin
        palette_y = img_with_border.height - round(border.bottom / 2) - round(color_palette.height / 2)

        if border_type == BorderType.POLAROID:
            # Text starts at border.left and must end before the palette begins.
            # Leave a gap (one palette_size cell) between text and palette. This is
            # now only a safety net - with the image-corner anchor the palette
            # rarely reaches the text - but it guarantees no overlap if a very long
            # EXIF string would still run into it.
            gap = palette_size
            available_text_width = max(50, palette_x - border.left - gap)

    # --- exif -------------------------------------------------------------
    if add_exif and exif:
        font_path = os.path.join(fontdir, font[0])
        bold_font_path = os.path.join(fontdir, boldfont[0])

        error_messages = [err for f in [(font_path, font[1]), (bold_font_path, boldfont[1])]
                          if (err := validate_font(fontpath=f[0], index=f[1]))]
        if len(error_messages) > 0:
            raise ValueError(error_messages)

        img_with_border = draw_exif(img_with_border, exif, border,
                                    (font_path, font[1]), (bold_font_path, boldfont[1]),
                                    available_width=available_text_width)
        save_as = f'{save_as}_exif'
    cb("exif", 0.55)

    # --- palette (overlay now, on top of the border) ----------------------
    if add_palette and color_palette is not None:
        img_with_border = overlay_palette(img=img_with_border,
                                          color_palette=color_palette,
                                          offset=(palette_x, palette_y))
        save_as = f'{save_as}_palette'
    cb("palette", 0.8)

    # --- save -------------------------------------------------------------
    # quality=95 + subsampling=0 keeps red edges sharp. See original notes.
    save_path = resolve_output_path(path, input_root, output_root, save_as, ext, overwrite=overwrite)
    exifdata = img.getexif()
    img_with_border.save(save_path, exif=exifdata, subsampling=0, quality=95)

    img_with_border.close()
    img.close()
    cb("save", 1.0)

    return save_path
