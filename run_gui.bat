@echo off
REM Launch the Photo Border app with no console window.
REM pythonw = windowed Python interpreter (no terminal).
REM If the app fails to start silently, use run_gui_debug.bat to see the error.
start "" pythonw gui.py
