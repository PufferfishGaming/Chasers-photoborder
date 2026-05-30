@echo off
REM Debug launcher: keeps the console open so startup errors are visible.
REM Use this if run_gui.bat (the silent one) does nothing when double-clicked.
python gui.py
echo.
echo --- App exited. If there's an error above, that's why. ---
pause
