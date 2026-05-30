# Chaser's PhotoBorder

A desktop and command-line tool that adds clean borders, EXIF strips, and colour
palettes to your photos. Point it at a photo or a whole folder, pick a border
style and aspect ratio, and it batches them in parallel while you watch a live
preview.

Built for photographers who want a tidy, consistent border + metadata strip on
exported shots — for example stage, dance, and portrait work straight off the
camera.

---

## Credits

This project is built on top of [**photoborder** by stevequinn](https://github.com/stevequinn/photoborder),
which provides the original border / EXIF / palette engine and CLI. That original
work is (c) 2024 stevequinn and released under the MIT License.

**Chaser's PhotoBorder** extends it with a desktop GUI and a number of additional
features (listed under *What this fork adds*). The project remains under
stevequinn's original MIT License — his copyright notice is preserved in the
`LICENSE` file, as MIT requires.

---

## What this fork adds

- **PySide6 desktop GUI** — folder/file pickers, options, and a live preview.
- **Live accurate preview** — renders the real pipeline (downscaled for speed),
  updating shortly after you change a setting.
- **Aspect-ratio padding** — pad output to 1:1, 4:5, 5:4, 3:2, 2:3, 16:9, 9:16, or
  a custom `W:H`, by adding border (never cropping).
- **Parallel batch processing** — uses multiple CPU cores for folders.
- **Background mode** — runs at low priority with fewer workers so the machine
  stays responsive during big batches.
- **~40x faster palette extraction** — colours are sampled from a downscaled copy
  of the image, with no visible change to the palette.
- **Separate output folder** with mirrored sub-folder structure (no accidental
  overwrites between identically-named files in different folders).
- **Don't-overwrite option** — append ` (1)`, ` (2)`, ... instead of clobbering
  existing outputs.
- **Remembers your settings** between launches.
- **Left-aligned EXIF + corner-anchored palette** that never collide, at any ratio.

---

## Installation

```bash
git clone https://github.com/PufferfishGaming/Chasers-photoborder
cd Chasers-photoborder
pip install -r requirements.txt
```

Dependencies: Pillow, extcolors, PySide6 (and pytest for the tests).

---

## Usage

### Desktop app

```bash
python gui.py
```

or double-click `run_gui.bat` (Windows). Use `run_gui_debug.bat` if the silent
launcher ever fails to start — it keeps a console open to show the error.

Workflow: choose an input file or folder, choose an output folder, pick a border
type and ratio, toggle EXIF / palette / options, check the preview, then
**Process**. A folder runs in parallel with a per-file progress bar; a single
file runs sequentially with per-stage progress.

### Command line

```bash
python main.py -t p -e -p -o output_folder Pictures\Waiting
```

| Option | Description |
| --- | --- |
| `-e, --exif` | Print photo EXIF data on the border |
| `-p, --palette` | Add a colour palette to the border |
| `-t, --border_type` | `p` polaroid, `s` small, `m` medium, `l` large, `i` instagram |
| `-r, --recursive` | Recurse into sub-folders |
| `-o, --output` | Output directory (default: a `bordered` folder next to the input) |
| `--ratio` | Target aspect ratio: `native`, `1:1`, `4:5`, `5:4`, `3:2`, `2:3`, `16:9`, `9:16`, or custom `W:H` |
| `--no-overwrite` | Append ` (1)`, ` (2)`, ... instead of overwriting existing outputs |
| `-f / -fv` | Regular font file / variant index |
| `-fb / -fbv` | Bold font file / variant index |
| `--include / --exclude` | Glob patterns for which files to process |

### Build a standalone Windows .exe

```bash
pip install pyinstaller
build_exe.bat
```

Produces `dist\Chaser's PhotoBorder.exe`. The `fonts/` folder is bundled
automatically.

---

## Project structure & functions

The code separates the GUI from a GUI-agnostic processing core, so the same
pipeline serves the desktop app, the CLI, and parallel workers.

### `core.py` — the processing pipeline (single source of truth)

- **`process_image(...)`** — the heart of the tool. Opens an image, draws the
  border, optionally draws EXIF text and a colour palette, and saves the result.
  Takes an optional progress callback (per-stage), an optional preview downscale,
  a target aspect ratio, and an overwrite flag. Used by the GUI, the CLI, and the
  parallel workers alike.
- **`resolve_output_path(...)`** — works out where to save, mirroring the input's
  sub-folder structure under the output root and (when overwriting is disabled)
  finding the next free ` (n)` name.
- **`STAGES`** — the ordered pipeline stages (`open`, `border`, `exif`, `palette`,
  `save`) used for progress reporting.

### `border.py` — border geometry and EXIF drawing

- **`BorderType`** — the border styles (polaroid, small, medium, large, instagram).
- **`get_border_size(...)`** — golden-ratio border thickness from image size.
- **`calculate_ratio_border(...)`** — border padding needed to reach a target
  ratio (the basis of the Instagram and aspect-ratio features).
- **`create_border(...)`** — builds the four border sizes for a given type, and
  applies aspect-ratio padding when requested.
- **`draw_border(...)`** — pastes the image onto a bordered canvas.
- **`draw_exif(...)`** — draws the EXIF text block. Left-aligns it for polaroid
  and shrinks the font when needed so it never overlaps the palette.

### `text.py` — font and text helpers

- **`load_font_variants` / `validate_font` / `create_font`** — font discovery,
  validation, and creation.
- **`draw_text_on_image(...)`** — draws a line of text, with optional centring and
  multi-line stacking.
- **`get_optimal_font_size(...)`** — largest font that fits a target height.
- **`get_optimal_font_size_constrained(...)`** — as above, but also constrained to
  a maximum width (used so EXIF text fits the space beside the palette).

### `palette.py` — colour palette extraction

- **`extract_colors(...)`** — extracts dominant colours, sampling from a downscaled
  copy for speed.
- **`render_color_platte(...)`** — renders the swatches into an image.
- **`overlay_palette(...)`** — pastes the palette onto the bordered image.
- **`load_image_color_palette(...)`** — convenience wrapper for the above.

### `exif.py` — EXIF extraction and formatting

- **`get_exif(...)`** — reads EXIF into a tidy dictionary.
- **`ExifItem`** — formats individual values (e.g. `Shot on ...`, `f/2.8`, `ISO640`).
- **`format_shutter_speed` / `format_focal_length`** — value formatters.

### `filemanager.py` — file selection

- **`should_include_file(...)`** — include/exclude glob matching.
- **`get_directory_files(...)`** — gathers matching files, optionally recursively.

### `worker.py` — parallel processing

- **`WorkerArgs` / `WorkerResult`** — picklable data passed to and from worker
  processes.
- **`process_one(...)`** — processes a single file in a worker; reports failures
  rather than crashing the batch.
- **`set_below_normal_priority()`** — lowers worker priority for Background mode.

### `gui.py` — the desktop application

- **`MainWindow`** — the app window: input/output pickers, options, live preview,
  progress bars, and settings persistence.
- **`PreviewWorker`** — renders the live preview on a background thread.
- **`BatchWorker`** — runs the batch, in parallel for folders or sequentially for a
  single file.

### `main.py` — the command-line entry point

- **`parse_arguments()`** — CLI options.
- **`parse_ratio(...)`** — parses ratio presets and custom `W:H` values.
- **`main()`** — gathers files and runs the pipeline.

---

## Fonts

Roboto (Regular & Medium) lives in `fonts/`. Add other TrueType fonts there and
select them with `-f` / `-fb`.

---

## Testing

```bash
pytest -s ./tests
```

---

## License

MIT License.

- (c) 2024 stevequinn — original [photoborder](https://github.com/stevequinn/photoborder) author.
- (c) 2025 PufferfishGaming — Chaser's PhotoBorder fork and additional features.

Both are released under the same MIT terms. See [`LICENSE`](LICENSE). Under MIT,
stevequinn's original copyright notice is retained; the fork's notice is added
alongside it, not in place of it.
