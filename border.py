"""
Image border functions and classes
"""
import math
from enum import Enum
from dataclasses import dataclass
from PIL import Image
import text as tm

class BorderType(Enum):
    POLAROID = 'p'
    SMALL = 's'
    MEDIUM = 'm'
    LARGE = 'l'
    INSTAGRAM = 'i'

    def __str__(self):
        return self.value

@dataclass
class Border:
    top: int
    right: int
    bottom: int
    left: int
    border_type: BorderType

def get_border_size(img_width: int, img_height: int, reduceby: int=4) -> int:
    """Calculate an image border size based on the golden ratio.

    Args:
        img_width (number): Source image width
        img_height (number): Source image height
        reduceby (int): Reduce the border by a factor of this

    Returns:
        int: The border size
    """
    # Use golden ratio to determine border size from image size.
    golden_ratio = (1 + 5 ** 0.5) / 2
    img_area = img_width * img_height
    canvas_area = img_area * golden_ratio
    border_size = math.ceil(math.sqrt(canvas_area - img_area) / reduceby)

    return border_size

def calculate_ratio_border(width, height, min_border=0, target_ratio=4/5) -> tuple[int, int]:
    """
    Given an image width and height, and a target_ratio, calculate the horizontal and vertical border pixel
    sizes needed to meet the target ratio.

    This is useful for matching 4/5 image ratios for instagram and the like.

    Args:
        width (int): The image width
        height (int): The image height
        min_border (int, optional): The minimum border to add to all sides. Defaults to 0.
        target_ratio (float, optional): The image ratio to match. Defaults to 4/5.

    Returns:
        tuple[int, int]: horizontal border pixels, vertical border pixels
    """
    current_ratio = width / height

    if current_ratio > target_ratio:
        # Image is too wide, add vertical borders
        new_height = max(height, math.ceil(width / target_ratio))
        vertical_border = max((new_height - height) // 2, min_border)
        horizontal_border = min_border
    else:
        # Image is too tall, add horizontal borders
        new_width = max(width, math.ceil(height * target_ratio))
        horizontal_border = max((new_width - width) // 2, min_border)
        vertical_border = min_border

    # Adjust to ensure the final image meets the target ratio
    final_width = width + 2 * horizontal_border
    final_height = height + 2 * vertical_border
    final_ratio = final_width / final_height

    if final_ratio > target_ratio:
        additional_vertical = math.ceil(final_width / target_ratio) - final_height
        vertical_border += additional_vertical // 2
    elif final_ratio < target_ratio:
        additional_horizontal = math.ceil(final_height * target_ratio) - final_width
        horizontal_border += additional_horizontal // 2

    return horizontal_border, vertical_border

def create_border(imgw: int, imgh: int, border_type: Border, target_ratio: float = None) -> Border:
    """Create a Border for an image.

    Args:
        imgw, imgh: Source image dimensions.
        border_type: The border style.
        target_ratio: Optional final canvas width/height ratio to pad TO (e.g. 0.8
                      for 4:5, 1.0 for square, 1.7778 for 16:9). None = native, no
                      ratio padding. The image is never cropped; extra border is
                      added to the deficient axis only. Ignored for INSTAGRAM,
                      which has its own fixed 4:5 behaviour.
    """
    # top, right, bottom, left
    reduceby_map = {
        BorderType.POLAROID: (32, 32, 6, 32),
        BorderType.SMALL: (32, 32, 32, 32),
        BorderType.MEDIUM: (16, 16, 16, 16),
        BorderType.LARGE: (6, 6, 6, 6),
        BorderType.INSTAGRAM: (32, 32, 32, 32)
    }
    rtop, rright, rbottom, rleft = reduceby_map[border_type]
    btop = get_border_size(imgw, imgh, rtop)
    bright = get_border_size(imgw, imgh, rright)
    bbottom = get_border_size(imgw, imgh, rbottom)
    bleft = get_border_size(imgw, imgh, rleft)

    if border_type == BorderType.INSTAGRAM:
        # In the case of instagram, we want to enforce an image ratio of 4/5 with a minimum border so the
        # non-padded sides also have a border.
        ratio_border_horizonal, ratio_border_vertical = calculate_ratio_border(imgw, imgh, min_border=btop)
        btop = ratio_border_vertical
        bright = ratio_border_horizonal
        bbottom = ratio_border_vertical
        bleft = ratio_border_horizonal
    elif target_ratio:
        # Generalised ratio padding for every other border type.
        #
        # We pad the canvas (image + the per-type borders already computed) out to
        # target_ratio by ADDING extra border to the deficient axis only. Padding is
        # added on top of the existing borders rather than replacing them, so the
        # border character of the type is preserved - crucially polaroid's large
        # asymmetric bottom border survives. The extra needed on an axis is split
        # evenly between its two sides.
        cur_w = imgw + bleft + bright
        cur_h = imgh + btop + bbottom
        cur_ratio = cur_w / cur_h

        if cur_ratio > target_ratio:
            # Too wide -> need more height. Add equally to top and bottom.
            target_h = math.ceil(cur_w / target_ratio)
            extra = max(0, target_h - cur_h)
            add_top = extra // 2
            add_bottom = extra - add_top
            btop += add_top
            bbottom += add_bottom
        elif cur_ratio < target_ratio:
            # Too tall -> need more width. Add equally to left and right.
            target_w = math.ceil(cur_h * target_ratio)
            extra = max(0, target_w - cur_w)
            add_left = extra // 2
            add_right = extra - add_left
            bleft += add_left
            bright += add_right

    border = Border(btop, bright, bbottom, bleft, border_type)

    return border

def draw_border(img: Image, border: Border) -> Image:
    w = img.width + border.left + border.right
    h = img.height + border.top + border.bottom
    canvas = Image.new("RGB", (w, h), (255, 255, 255, 0))
    canvas.paste(img, (border.left, border.top))

    return canvas

def draw_exif(img: Image, exif: dict, border: Border, font: tuple[str, int], boldfont: tuple[str, int],
              available_width: int = None) -> Image:
    """Draw EXIF text on the bottom border.

    Args:
        available_width: If set, the font size is reduced so that every text line
                         fits within this many pixels. Used for the left-aligned
                         polaroid layout so the text never overlaps the palette
                         that occupies the bottom-right. None = no width limit.
    """
    centered = border.border_type in (BorderType.POLAROID, BorderType.LARGE, BorderType.INSTAGRAM)
    multiplier = 0.2 if centered else 0.5

    # Build the three lines up front so we can size against their widths.
    line_heading = f"{exif['Make']} {exif['Model']}"
    line_lens = f"{exif['LensMake']} {exif['LensModel']}"
    line_settings = f"{exif['FocalLength']}  {exif['FNumber']}  {exif['ISOSpeedRatings']}  {exif['ExposureTime']}"

    # Height-and-width constrained sizing. The heading uses the bold font; the two
    # body lines use the regular font. We size each against the width budget.
    font_size = tm.get_optimal_font_size_constrained(
        [line_lens, line_settings], border.bottom * multiplier, available_width,
        font[0], index=font[1])
    heading_font_size = tm.get_optimal_font_size_constrained(
        [line_heading], border.bottom * (multiplier + 0.02), available_width,
        boldfont[0], index=boldfont[1])
    font = tm.create_font(font_size, fontpath=font[0], index=font[1])
    heading_font = tm.create_font(heading_font_size, fontpath=boldfont[0], index=boldfont[1])

    stack_lines = centered
    horizontally_centered = centered and border.border_type != BorderType.POLAROID

    # Vertical align text in bottom border based on total font block height.
    if stack_lines:
         # 3 Lines of text. 1 heading, two normal. Minus heading margins. A bit sketchy but it aligns fine.
        total_font_height = heading_font.size + (2 * font.size) - (heading_font.size / 2)
        y = img.height - border.bottom + \
            (border.bottom / 2) - (total_font_height / 2)
    else:
        # y = img.height - (border.bottom / 2) - (heading_font.size / 3)
        y = img.height - (border.bottom / 2) + (heading_font.size / 3)

    x = border.left

    text_img, (x, y) = tm.draw_text_on_image(img, line_heading, (x, y), horizontally_centered, heading_font,
                                             fill=(100, 100, 100), stack_lines=stack_lines)

    text_img, (x, y) = tm.draw_text_on_image(text_img, line_lens, (x, y), horizontally_centered, font,
                                             fill=(128, 128, 128), stack_lines=stack_lines)

    text_img, (x, y) = tm.draw_text_on_image(text_img, line_settings, (x, y), horizontally_centered, font,
                                             fill=(128, 128, 128), stack_lines=stack_lines)

    return text_img
