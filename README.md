# Photo Border

Add a border, EXIF strip, and colour palette to JPEG/PNG photos — from the
command line or a Windows desktop app.

## What changed in this version

- **Desktop GUI** (`gui.py`, PySide6) with live preview and progress bars.
- **Separate output folder.** Outputs no longer land next to the originals;
  they go into a chosen output folder, mirroring any sub-folder structure so
  identically-named files in different folders don't overwrite each other.
- **~40× faster palette extraction.** Colours are now sampled from a downscaled
  copy of the image (the dominant colours are visually identical). On a 24MP
  photo this took palette extraction from ~22s to ~0.5s in testing.
- **Parallel batch processing** across CPU cores for folders.
- The CLI (`main.py`) still works, now with a `-o/--output` option.

## Install

```bash
pip install -r requirements.txt
```

## Desktop app

```bash
python gui.py
```

or double-click `run_gui.bat` on Windows.

- **Choose file / folder** for input, and an **output folder**.
- Pick the border type, toggle EXIF and palette.
- The **preview** shows the real pipeline output (downscaled for display),
  updating ~0.4s after you change a setting.
- **Process** runs the batch. A folder runs in parallel (per-file progress);
  a single file runs sequentially (per-stage progress).

### Build a standalone .exe

```bash
pip install pyinstaller
build_exe.bat
```

Produces `dist\PhotoBorder.exe`. The `fonts/` folder is bundled automatically.

## Command line

```bash
python main.py -t p -e -p -o output_folder Pictures\Waiting
```

```
options:
  -e, --exif              Print photo exif data on the border
  -p, --palette           Add colour palette to the photo border
  -t, --border_type       p polaroid, s small, m medium, l large, i instagram (default: s)
  -r, --recursive         Recurse into sub-folders
  -o, --output            Output directory (default: a "bordered" folder next to input)
  -f / -fv                Regular font file / variant index
  -fb / -fbv              Bold font file / variant index
  --include / --exclude   Glob patterns
```

## Performance notes (honest caveats)

- **GPU acceleration was considered and deliberately not used.** The bottleneck
  was palette extraction (pixel clustering) and JPEG encoding; neither maps to a
  GPU without either changing the palette algorithm (different output) or adding
  a fragile CUDA dependency that complicates packaging. The downscale-before-
  extract change captured the large win on the CPU instead.
- **SIMD:** `pillow-simd` (an AVX2-compiled Pillow drop-in) can speed up resize
  and JPEG encode further, but it is hard to install on Windows (build toolchain,
  scarce wheels). Recommended only on Linux. No code change needed if used.
- **Per-stage vs. parallel progress:** folder runs are parallel and report
  per-*file* progress only — `ProcessPoolExecutor` workers can't stream per-stage
  updates across the process boundary. Single-file runs are sequential and show
  per-*stage* progress.
- **Cancel** during a parallel batch stops scheduling/reporting; files already
  in flight in worker processes finish on their own.

## Fonts

Roboto (Regular & Medium) live in `fonts/`. Add other TTFs there and pass them
with `-f` / `-fb`.

## Testing

```bash
pytest -s ./tests
```
