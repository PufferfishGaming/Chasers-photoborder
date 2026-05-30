"""
Parallel worker entry point.

ProcessPoolExecutor on Windows uses 'spawn', which re-imports this module in
each worker process and requires the target callable and its arguments to be
picklable. That rules out passing Qt signals, file handles or callbacks across
the boundary - so the worker calls process_image with progress_cb=None and
returns a plain result tuple the parent can use to advance a per-file bar.
"""
from dataclasses import dataclass
from border import BorderType
from core import process_image


def set_below_normal_priority():
    """Lower the CURRENT process's scheduling priority.

    Used as a ProcessPoolExecutor initializer so each worker starts at reduced
    priority. On Windows this is BELOW_NORMAL_PRIORITY_CLASS, which lets the
    Thread Director scheduler preferentially run this work on E-cores and yield
    P-cores to foreground apps - giving a responsive machine during big batches
    without fragile manual core-affinity pinning. On other platforms it falls
    back to a positive nice value. All failures are swallowed: priority is an
    optimisation, never a correctness requirement.
    """
    import sys
    try:
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
        else:
            import os
            os.nice(10)
    except Exception:
        pass


@dataclass
class WorkerArgs:
    """Everything needed to process one file, all picklable primitives."""
    path: str
    add_exif: bool
    add_palette: bool
    border_type_value: str           # BorderType stored as its .value char ('p','s',...)
    font: tuple                      # (filename, variant_index)
    boldfont: tuple
    fontdir: str
    output_root: str
    input_root: str
    target_ratio: float = None
    overwrite: bool = True


@dataclass
class WorkerResult:
    path: str                        # source path
    save_path: str = None            # output path on success
    error: str = None                # error string on failure


def process_one(args: WorkerArgs) -> WorkerResult:
    """Process a single file. Never raises across the process boundary -
    failures are returned as WorkerResult.error so one bad file does not kill
    the whole batch."""
    try:
        save_path = process_image(
            path=args.path,
            add_exif=args.add_exif,
            add_palette=args.add_palette,
            border_type=BorderType(args.border_type_value),
            font=args.font,
            boldfont=args.boldfont,
            fontdir=args.fontdir,
            output_root=args.output_root,
            input_root=args.input_root,
            progress_cb=None,
            preview_max_edge=None,
            target_ratio=args.target_ratio,
            overwrite=args.overwrite,
        )
        return WorkerResult(path=args.path, save_path=save_path)
    except Exception as e:  # noqa: BLE001 - intentionally broad, reported not raised
        return WorkerResult(path=args.path, error=f"{type(e).__name__}: {e}")
