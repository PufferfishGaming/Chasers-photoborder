@echo off
REM Build a standalone Windows .exe of Chaser's PhotoBorder GUI.
REM Requires: pip install pyinstaller
REM
REM Notes:
REM  - The fonts/ folder is bundled so EXIF rendering works in the packaged app.
REM  - --noconsole hides the terminal window; remove it if you want to see logs.
REM  - multiprocessing (the parallel batch path) needs freeze_support(), which
REM    gui.main() already calls, so the .exe will spawn workers correctly.

pyinstaller --noconfirm --onefile --noconsole ^
  --name "Chaser's PhotoBorder" ^
  --add-data "fonts;fonts" ^
  gui.py

echo.
echo Built "dist\Chaser's PhotoBorder.exe"
