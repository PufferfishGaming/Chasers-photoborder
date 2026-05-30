"""
Photo Border - Windows 11 desktop frontend (PySide6).

Architecture
------------
Two clearly separated execution paths share one pipeline (core.process_image):

  Preview path   : single image, runs the REAL full-res pipeline on a QThread,
                   debounced ~400ms, then downscales only the finished result for
                   display. Accurate (it is literally the output, shown smaller).
                   Emits per-stage progress.

  Batch path     : a folder of images. Uses ProcessPoolExecutor to process files
                   in parallel across CPU cores. Per-FILE progress only (parallel
                   workers cannot stream per-stage progress across the process
                   boundary). One bad file is reported, not fatal.

  Single-file    : if the user points the batch at one file, it is processed
  batch          : sequentially in-thread so per-stage progress is still shown.

Output always goes to a chosen output folder, mirroring input sub-folder
structure to avoid same-name collisions.
"""
import os
import sys
import time
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

from PySide6 import QtCore, QtGui, QtWidgets

from border import BorderType
from core import process_image, STAGES
from filemanager import should_include_file, get_directory_files
from worker import WorkerArgs, WorkerResult, process_one, set_below_normal_priority

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INCLUDE = ['*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG']
EXCLUDE = ['*_border*']
PREVIEW_DISPLAY_EDGE = 900   # px, longest edge of the displayed preview

BORDER_LABELS = {
    BorderType.POLAROID: "Polaroid",
    BorderType.SMALL: "Small",
    BorderType.MEDIUM: "Medium",
    BorderType.LARGE: "Large",
    BorderType.INSTAGRAM: "Instagram",
}

# Aspect-ratio presets: label -> width/height float (None = native, no padding).
RATIO_PRESETS = [
    ("Native (no padding)", None),
    ("1:1 Square", 1.0),
    ("4:5 Portrait", 4 / 5),
    ("5:4 Landscape", 5 / 4),
    ("3:2", 3 / 2),
    ("2:3", 2 / 3),
    ("16:9 Wide", 16 / 9),
    ("9:16 Tall", 9 / 16),
]


def module_fontdir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


# ----------------------------------------------------------------------------
# Preview worker: runs the real pipeline on ONE file in a background thread.
# ----------------------------------------------------------------------------
class PreviewWorker(QtCore.QThread):
    stage = QtCore.Signal(str, float)        # stage name, fraction
    done = QtCore.Signal(str)                # output path of rendered preview
    failed = QtCore.Signal(str)

    def __init__(self, params: dict, tmp_out: str):
        super().__init__()
        self.params = params
        self.tmp_out = tmp_out
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        p = self.params

        def cb(stage_name, frac):
            if self._cancelled:
                # Cooperative cancel: raise to unwind out of the pipeline.
                raise _Cancelled()
            self.stage.emit(stage_name, frac)

        try:
            out = process_image(
                path=p["path"],
                add_exif=p["add_exif"],
                add_palette=p["add_palette"],
                border_type=p["border_type"],
                font=p["font"],
                boldfont=p["boldfont"],
                fontdir=p["fontdir"],
                output_root=self.tmp_out,
                input_root=os.path.dirname(p["path"]),
                progress_cb=cb,
                target_ratio=p.get("target_ratio"),
                preview_max_edge=p.get("preview_max_edge"),
            )
            if self._cancelled:
                return
            self.done.emit(out)
        except _Cancelled:
            return
        except Exception as e:  # noqa: BLE001
            if not self._cancelled:
                self.failed.emit(f"{type(e).__name__}: {e}")


class _Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------
# Batch worker: parallel folder processing OR sequential single-file.
# ----------------------------------------------------------------------------
class BatchWorker(QtCore.QThread):
    file_done = QtCore.Signal(int, int, str, str)   # done_count, total, src, result_msg
    stage = QtCore.Signal(str, float)               # used only in sequential mode
    finished_all = QtCore.Signal(int, int)          # success_count, fail_count
    failed = QtCore.Signal(str)

    def __init__(self, paths, params: dict, output_root: str, input_root: str, max_workers: int,
                 background: bool = False):
        super().__init__()
        self.paths = paths
        self.params = params
        self.output_root = output_root
        self.input_root = input_root
        self.max_workers = max_workers
        self.background = background
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self.paths)
        if total == 0:
            self.finished_all.emit(0, 0)
            return

        p = self.params
        # Single file -> sequential, keep per-stage progress.
        if total == 1:
            self._run_sequential(total)
        else:
            self._run_parallel(total)

    def _run_sequential(self, total):
        p = self.params
        success = fail = 0

        def cb(stage_name, frac):
            if self._cancelled:
                raise _Cancelled()
            self.stage.emit(stage_name, frac)

        for i, path in enumerate(self.paths):
            if self._cancelled:
                break
            try:
                out = process_image(
                    path=path, add_exif=p["add_exif"], add_palette=p["add_palette"],
                    border_type=p["border_type"], font=p["font"], boldfont=p["boldfont"],
                    fontdir=p["fontdir"], output_root=self.output_root,
                    input_root=self.input_root, progress_cb=cb,
                    target_ratio=p.get("target_ratio"),
                    overwrite=p.get("overwrite", True),
                )
                success += 1
                self.file_done.emit(i + 1, total, path, f"Saved: {os.path.basename(out)}")
            except _Cancelled:
                break
            except Exception as e:  # noqa: BLE001
                fail += 1
                self.file_done.emit(i + 1, total, path, f"ERROR: {e}")
        self.finished_all.emit(success, fail)

    def _run_parallel(self, total):
        p = self.params
        success = fail = 0
        done = 0
        args_list = [
            WorkerArgs(
                path=path, add_exif=p["add_exif"], add_palette=p["add_palette"],
                border_type_value=p["border_type"].value, font=p["font"],
                boldfont=p["boldfont"], fontdir=p["fontdir"],
                output_root=self.output_root, input_root=self.input_root,
                target_ratio=p.get("target_ratio"),
                overwrite=p.get("overwrite", True),
            )
            for path in self.paths
        ]
        try:
            initializer = set_below_normal_priority if self.background else None
            with ProcessPoolExecutor(max_workers=self.max_workers,
                                     initializer=initializer) as ex:
                futures = {ex.submit(process_one, a): a.path for a in args_list}
                for fut in as_completed(futures):
                    if self._cancelled:
                        # Best-effort: stop scheduling/handling more. In-flight
                        # workers finish on their own; we just stop reporting.
                        break
                    res: WorkerResult = fut.result()
                    done += 1
                    if res.error:
                        fail += 1
                        self.file_done.emit(done, total, res.path, f"ERROR: {res.error}")
                    else:
                        success += 1
                        self.file_done.emit(done, total, res.path,
                                            f"Saved: {os.path.basename(res.save_path)}")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.finished_all.emit(success, fail)


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chaser's PhotoBorder")
        self.resize(1180, 760)

        # Persistent settings (Windows: registry under Chaser/PhotoBorder).
        self.settings = QtCore.QSettings("Chaser", "PhotoBorder")

        self.input_path = None       # file or folder
        self.input_is_dir = False
        self.output_root = None
        self.preview_source = None   # single image used for preview
        self.preview_worker = None
        self.batch_worker = None
        self.tmp_preview_dir = os.path.join(
            QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.TempLocation),
            "photoborder_preview",
        )
        os.makedirs(self.tmp_preview_dir, exist_ok=True)

        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self._start_preview)

        self._build_ui()
        self._apply_style()
        self._load_settings()

    # ---- UI construction ----------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left control panel
        panel = QtWidgets.QWidget()
        panel.setObjectName("panel")
        panel.setFixedWidth(380)
        pl = QtWidgets.QVBoxLayout(panel)
        pl.setContentsMargins(24, 24, 24, 24)
        pl.setSpacing(14)

        title = QtWidgets.QLabel("Photo Border")
        title.setObjectName("title")
        pl.addWidget(title)
        sub = QtWidgets.QLabel("Borders, EXIF & palette for your photos")
        sub.setObjectName("subtitle")
        pl.addWidget(sub)
        pl.addSpacing(8)

        # Input selection
        self.input_label = QtWidgets.QLabel("No input selected")
        self.input_label.setObjectName("pathlabel")
        self.input_label.setWordWrap(True)
        btn_row = QtWidgets.QHBoxLayout()
        b_file = QtWidgets.QPushButton("Choose file…")
        b_file.clicked.connect(self.choose_file)
        b_dir = QtWidgets.QPushButton("Choose folder…")
        b_dir.clicked.connect(self.choose_folder)
        btn_row.addWidget(b_file)
        btn_row.addWidget(b_dir)
        pl.addWidget(self._section("Input"))
        pl.addWidget(self.input_label)
        pl.addLayout(btn_row)

        # Output selection
        self.output_label = QtWidgets.QLabel("Not set")
        self.output_label.setObjectName("pathlabel")
        self.output_label.setWordWrap(True)
        b_out = QtWidgets.QPushButton("Choose output folder…")
        b_out.clicked.connect(self.choose_output)
        pl.addWidget(self._section("Output folder"))
        pl.addWidget(self.output_label)
        pl.addWidget(b_out)

        # Options
        pl.addWidget(self._section("Options"))
        self.border_combo = QtWidgets.QComboBox()
        for bt in BorderType:
            self.border_combo.addItem(BORDER_LABELS[bt], bt)
        self.border_combo.setCurrentIndex(list(BorderType).index(BorderType.POLAROID))
        self.border_combo.currentIndexChanged.connect(self._schedule_preview)
        self.border_combo.currentIndexChanged.connect(self._update_ratio_hint)
        bl = QtWidgets.QHBoxLayout()
        bl.addWidget(QtWidgets.QLabel("Border"))
        bl.addWidget(self.border_combo, 1)
        pl.addLayout(bl)

        self.ratio_combo = QtWidgets.QComboBox()
        for label, val in RATIO_PRESETS:
            self.ratio_combo.addItem(label, val)
        self.ratio_combo.currentIndexChanged.connect(self._on_ratio_changed)
        rl_row = QtWidgets.QHBoxLayout()
        rl_row.addWidget(QtWidgets.QLabel("Ratio"))
        rl_row.addWidget(self.ratio_combo, 1)
        pl.addLayout(rl_row)
        # Hint shown when ratio is overridden by the Instagram border type.
        self.ratio_hint = QtWidgets.QLabel("")
        self.ratio_hint.setObjectName("hint")
        self.ratio_hint.setWordWrap(True)
        pl.addWidget(self.ratio_hint)

        self.cb_exif = QtWidgets.QCheckBox("Print EXIF on border")
        self.cb_exif.setChecked(True)
        self.cb_exif.stateChanged.connect(self._schedule_preview)
        pl.addWidget(self.cb_exif)

        self.cb_palette = QtWidgets.QCheckBox("Add colour palette")
        self.cb_palette.setChecked(True)
        self.cb_palette.stateChanged.connect(self._schedule_preview)
        pl.addWidget(self.cb_palette)

        self.cb_recursive = QtWidgets.QCheckBox("Recurse into sub-folders")
        pl.addWidget(self.cb_recursive)

        self.cb_no_overwrite = QtWidgets.QCheckBox("Don't overwrite existing files")
        self.cb_no_overwrite.setToolTip(
            "If an output file with the same name already exists (e.g. from a "
            "previous run), append ' (1)', ' (2)', etc. instead of overwriting it.")
        pl.addWidget(self.cb_no_overwrite)

        # Parallelism
        wrow = QtWidgets.QHBoxLayout()
        wrow.addWidget(QtWidgets.QLabel("Parallel workers"))
        self.workers_spin = QtWidgets.QSpinBox()
        cpu = max(1, os.cpu_count() or 1)
        self.workers_spin.setRange(1, cpu)
        # Default to roughly half the logical cores: enough for good throughput
        # without saturating the whole machine. (Using ALL logical cores tends to
        # add cache/memory pressure with little throughput gain for this workload.)
        self.workers_spin.setValue(max(1, cpu // 2))
        wrow.addWidget(self.workers_spin)
        pl.addLayout(wrow)

        # Background mode: low priority + reduced workers so the machine stays
        # responsive during big batches (Windows parks low-priority work on E-cores).
        self.cb_background = QtWidgets.QCheckBox("Background mode (stay responsive)")
        self.cb_background.setToolTip(
            "Runs processing at reduced priority and fewer workers so you can keep "
            "using your PC during large batches. Slightly slower overall.")
        self.cb_background.stateChanged.connect(self._on_background_toggled)
        pl.addWidget(self.cb_background)
        self.background_hint = QtWidgets.QLabel("")
        self.background_hint.setObjectName("hint")
        self.background_hint.setWordWrap(True)
        pl.addWidget(self.background_hint)

        pl.addStretch(1)

        # Progress
        self.stage_bar = QtWidgets.QProgressBar()
        self.stage_bar.setRange(0, 100)
        self.stage_bar.setFormat("%p%  —  idle")
        pl.addWidget(self._section("Progress"))
        pl.addWidget(QtWidgets.QLabel("Current file (stages)"))
        pl.addWidget(self.stage_bar)
        self.file_bar = QtWidgets.QProgressBar()
        self.file_bar.setRange(0, 100)
        self.file_bar.setFormat("%v / %m files")
        pl.addWidget(QtWidgets.QLabel("Batch (files)"))
        pl.addWidget(self.file_bar)

        # Action buttons
        act = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("Process")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.start_batch)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_batch)
        act.addWidget(self.run_btn, 2)
        act.addWidget(self.cancel_btn, 1)
        pl.addLayout(act)

        root.addWidget(panel)

        # Right preview area
        right = QtWidgets.QWidget()
        right.setObjectName("preview_area")
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(24, 24, 24, 24)
        self.preview_status = QtWidgets.QLabel("Select an image to preview")
        self.preview_status.setObjectName("preview_status")
        self.preview_status.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumSize(400, 400)
        rl.addWidget(self.preview_status)
        rl.addWidget(self.preview_label, 1)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("log")
        self.log.setFixedHeight(120)
        rl.addWidget(self.log)
        root.addWidget(right, 1)

    def _section(self, text):
        lbl = QtWidgets.QLabel(text.upper())
        lbl.setObjectName("section")
        return lbl

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget { color: #e8e8ea; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
            #panel { background: #1b1c20; border-right: 1px solid #2c2e34; }
            #preview_area { background: #232428; }
            #title { font-size: 22px; font-weight: 600; color: #f4f4f6; }
            #subtitle { color: #8a8d96; font-size: 12px; }
            #hint { color: #c89b5a; font-size: 11px; font-style: italic; }
            #section { color: #6f727b; font-size: 10px; font-weight: 700; letter-spacing: 1px; margin-top: 6px; }
            #pathlabel { color: #b9bcc4; font-size: 11px; background: #16171a; border: 1px solid #2c2e34; border-radius: 6px; padding: 6px 8px; }
            #preview_status { color: #8a8d96; font-size: 12px; }
            QPushButton { background: #2a2c32; border: 1px solid #3a3d45; border-radius: 6px; padding: 7px 10px; }
            QPushButton:hover { background: #33363d; }
            QPushButton:disabled { color: #555; background: #202126; }
            QPushButton#primary { background: #3b6ea5; border: none; font-weight: 600; }
            QPushButton#primary:hover { background: #447dbb; }
            QComboBox { background: #16171a; border: 1px solid #2c2e34; border-radius: 6px; padding: 5px 8px; }
            /* Spinbox: padding only on the left so the text field never extends
               over the up/down buttons. The buttons get their own explicit
               sub-control region on the right, with a comfortable click target. */
            QSpinBox { background: #16171a; border: 1px solid #2c2e34; border-radius: 6px; padding: 5px 0px 5px 8px; }
            QSpinBox::up-button {
                subcontrol-origin: border; subcontrol-position: top right;
                width: 22px; height: 14px; border-left: 1px solid #2c2e34;
                border-top-right-radius: 6px; background: #2a2c32;
            }
            QSpinBox::down-button {
                subcontrol-origin: border; subcontrol-position: bottom right;
                width: 22px; height: 14px; border-left: 1px solid #2c2e34;
                border-bottom-right-radius: 6px; background: #2a2c32;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #3a3d45; }
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed { background: #447dbb; }
            QSpinBox::up-arrow { image: none; width: 0; height: 0;
                border-left: 4px solid transparent; border-right: 4px solid transparent;
                border-bottom: 5px solid #c8cad0; }
            QSpinBox::down-arrow { image: none; width: 0; height: 0;
                border-left: 4px solid transparent; border-right: 4px solid transparent;
                border-top: 5px solid #c8cad0; }
            QCheckBox { spacing: 8px; }
            QProgressBar { background: #16171a; border: 1px solid #2c2e34; border-radius: 6px; text-align: center; height: 20px; }
            QProgressBar::chunk { background: #3b6ea5; border-radius: 5px; }
            #log { background: #16171a; border: 1px solid #2c2e34; border-radius: 6px; font-family: 'Consolas', monospace; font-size: 11px; color: #9aa; }
        """)

    # ---- input/output selection --------------------------------------------
    def choose_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose image", "", "Images (*.jpg *.jpeg *.png)")
        if path:
            self.input_path = path
            self.input_is_dir = False
            self.input_label.setText(path)
            self.preview_source = path
            self._default_output_for(os.path.dirname(path))
            self._schedule_preview()

    def choose_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder")
        if path:
            self.input_path = path
            self.input_is_dir = True
            self.input_label.setText(path)
            self._default_output_for(path)
            # Pick first matching image for preview.
            files = get_directory_files(path, self.cb_recursive.isChecked(), INCLUDE, EXCLUDE)
            self.preview_source = files[0] if files else None
            if self.preview_source:
                self._schedule_preview()
            else:
                self.preview_status.setText("No matching images in folder")

    def choose_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_root = path
            self.output_label.setText(path)

    def _default_output_for(self, base):
        if not self.output_root:
            self.output_root = os.path.join(base, "bordered")
            self.output_label.setText(self.output_root)

    # ---- preview ------------------------------------------------------------
    def _on_background_toggled(self):
        on = self.cb_background.isChecked()
        # In background mode we override the worker count to a low fixed value and
        # disable the spinbox so there is one source of truth at a time.
        self.workers_spin.setEnabled(not on)
        if on:
            cpu = max(1, os.cpu_count() or 1)
            bg = self._background_worker_count()
            self.background_hint.setText(
                f"Low priority, {bg} of {cpu} workers. Machine stays responsive; batch is slower.")
        else:
            self.background_hint.setText("")

    def _background_worker_count(self):
        # A quarter of logical cores, at least 1, capped at 4 - a small footprint
        # that leaves the machine usable.
        cpu = max(1, os.cpu_count() or 1)
        return max(1, min(4, cpu // 4))

    def _effective_workers(self):
        if self.cb_background.isChecked():
            return self._background_worker_count()
        return self.workers_spin.value()

    def _current_params(self):
        return {
            "add_exif": self.cb_exif.isChecked(),
            "add_palette": self.cb_palette.isChecked(),
            "border_type": self.border_combo.currentData(),
            "target_ratio": self.ratio_combo.currentData(),
            "font": ("Roboto-Regular.ttf", 0),
            "boldfont": ("Roboto-Medium.ttf", 0),
            "fontdir": module_fontdir(),
        }

    def _on_ratio_changed(self):
        self._update_ratio_hint()
        self._schedule_preview()

    def _update_ratio_hint(self):
        # Instagram border type enforces its own 4:5 and ignores the ratio control.
        is_instagram = self.border_combo.currentData() == BorderType.INSTAGRAM
        ratio_set = self.ratio_combo.currentData() is not None
        if is_instagram and ratio_set:
            self.ratio_hint.setText("Instagram border enforces 4:5; the Ratio setting is ignored.")
        else:
            self.ratio_hint.setText("")

    def _schedule_preview(self):
        if self.preview_source:
            self._debounce.start()   # restart -> debounce

    def _start_preview(self):
        if not self.preview_source:
            return
        # Cancel any in-flight preview render.
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.cancel()
            self.preview_worker.wait(2000)

        params = self._current_params()
        params["path"] = self.preview_source
        # Render previews from a downscaled source so a full 33MP file (or a huge
        # wide-ratio canvas) renders in a fraction of a second instead of grinding
        # at full resolution. Border proportions differ by <1% from the full-res
        # output, which is imperceptible in a preview.
        params["preview_max_edge"] = PREVIEW_DISPLAY_EDGE
        self.preview_status.setText("Rendering preview…")
        self.stage_bar.setFormat("%p%  —  preview")
        self.preview_worker = PreviewWorker(params, self.tmp_preview_dir)
        self.preview_worker.stage.connect(self._on_stage)
        self.preview_worker.done.connect(self._on_preview_done)
        self.preview_worker.failed.connect(self._on_preview_failed)
        self.preview_worker.start()

    def _on_stage(self, stage, frac):
        self.stage_bar.setValue(int(frac * 100))
        self.stage_bar.setFormat(f"%p%  —  {stage}")

    def _on_preview_done(self, out_path):
        pix = QtGui.QPixmap(out_path)
        if pix.isNull():
            self.preview_status.setText("Preview failed to load")
            return
        scaled = pix.scaled(
            self.preview_label.width(), self.preview_label.height(),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)
        self.preview_status.setText(f"Preview · {os.path.basename(self.preview_source)}")
        self.stage_bar.setFormat("%p%  —  preview ready")

    def _on_preview_failed(self, msg):
        self.preview_status.setText(f"Preview error: {msg}")
        self._log(f"Preview error: {msg}")

    # ---- batch processing ---------------------------------------------------
    def start_batch(self):
        if not self.input_path:
            self._log("No input selected.")
            return
        if not self.output_root:
            self._log("No output folder selected.")
            return

        if self.input_is_dir:
            input_root = os.path.abspath(self.input_path)
            paths = get_directory_files(self.input_path, self.cb_recursive.isChecked(), INCLUDE, EXCLUDE)
        else:
            input_root = os.path.dirname(os.path.abspath(self.input_path))
            paths = [self.input_path] if should_include_file(self.input_path, INCLUDE, EXCLUDE) else []

        if not paths:
            self._log("No images matched.")
            return

        os.makedirs(self.output_root, exist_ok=True)
        params = self._current_params()
        params["overwrite"] = not self.cb_no_overwrite.isChecked()
        self.file_bar.setRange(0, len(paths))
        self.file_bar.setValue(0)
        self._log(f"Processing {len(paths)} file(s) → {self.output_root}")
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self.batch_worker = BatchWorker(
            paths, params, self.output_root, input_root,
            self._effective_workers(), background=self.cb_background.isChecked())
        self.batch_worker.file_done.connect(self._on_file_done)
        self.batch_worker.stage.connect(self._on_stage)
        self.batch_worker.finished_all.connect(self._on_batch_finished)
        self.batch_worker.failed.connect(self._on_preview_failed)
        self.batch_worker.start()

    def _on_file_done(self, done, total, src, msg):
        self.file_bar.setValue(done)
        self._log(f"[{done}/{total}] {os.path.basename(src)} — {msg}")

    def _on_batch_finished(self, success, fail):
        self._log(f"Done. {success} succeeded, {fail} failed.")
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.stage_bar.setValue(0)
        self.stage_bar.setFormat("%p%  —  idle")

    def cancel_batch(self):
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.cancel()
            self._log("Cancelling… (in-flight files will finish)")

    def _log(self, msg):
        self.log.appendPlainText(msg)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Re-scale current preview pixmap to the new size if present.
        pm = self.preview_label.pixmap()
        if pm and not pm.isNull():
            self.preview_label.setPixmap(pm.scaled(
                self.preview_label.width(), self.preview_label.height(),
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    def closeEvent(self, e):
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.cancel()
            self.preview_worker.wait(2000)
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.cancel()
            self.batch_worker.wait(3000)
        self._save_settings()
        super().closeEvent(e)

    # ---- settings persistence ----------------------------------------------
    def _save_settings(self):
        s = self.settings
        s.setValue("input_path", self.input_path or "")
        s.setValue("input_is_dir", self.input_is_dir)
        s.setValue("output_root", self.output_root or "")
        s.setValue("border_index", self.border_combo.currentIndex())
        s.setValue("ratio_index", self.ratio_combo.currentIndex())
        s.setValue("exif", self.cb_exif.isChecked())
        s.setValue("palette", self.cb_palette.isChecked())
        s.setValue("recursive", self.cb_recursive.isChecked())
        s.setValue("no_overwrite", self.cb_no_overwrite.isChecked())
        s.setValue("background", self.cb_background.isChecked())
        s.setValue("workers", self.workers_spin.value())
        s.sync()

    def _load_settings(self):
        s = self.settings

        def get_bool(key, default):
            v = s.value(key, default)
            # QSettings may return strings ('true'/'false') depending on platform.
            if isinstance(v, str):
                return v.lower() == "true"
            return bool(v)

        # Options first (always safe to restore).
        bi = s.value("border_index", None)
        if bi is not None:
            try:
                self.border_combo.setCurrentIndex(int(bi))
            except (ValueError, TypeError):
                pass
        ri = s.value("ratio_index", None)
        if ri is not None:
            try:
                self.ratio_combo.setCurrentIndex(int(ri))
            except (ValueError, TypeError):
                pass

        self.cb_exif.setChecked(get_bool("exif", True))
        self.cb_palette.setChecked(get_bool("palette", True))
        self.cb_recursive.setChecked(get_bool("recursive", False))
        self.cb_no_overwrite.setChecked(get_bool("no_overwrite", False))
        self.cb_background.setChecked(get_bool("background", False))

        w = s.value("workers", None)
        if w is not None:
            try:
                self.workers_spin.setValue(int(w))
            except (ValueError, TypeError):
                pass
        # Re-apply background toggle side effects (disables spinbox + hint).
        self._on_background_toggled()
        self._update_ratio_hint()

        # Paths last, and only if they still exist - a stale path (unplugged
        # drive, deleted folder) must NOT be restored, or it could break the
        # preview on launch. Silently skip anything missing.
        out = s.value("output_root", "")
        if out and os.path.isdir(out):
            self.output_root = out
            self.output_label.setText(out)

        inp = s.value("input_path", "")
        is_dir = get_bool("input_is_dir", False)
        if inp and os.path.exists(inp):
            self.input_path = inp
            self.input_is_dir = is_dir
            self.input_label.setText(inp)
            if is_dir:
                files = get_directory_files(inp, self.cb_recursive.isChecked(), INCLUDE, EXCLUDE)
                self.preview_source = files[0] if files else None
            else:
                self.preview_source = inp
            if self.preview_source:
                self._schedule_preview()


def main():
    # Required for ProcessPoolExecutor under PyInstaller/spawn on Windows.
    from multiprocessing import freeze_support
    freeze_support()
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
