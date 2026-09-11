@echo off
REM Build a single standalone FotoRenamer.exe (no Python needed to run it).
REM Takes a couple of minutes the first time. Result: dist\FotoRenamer.exe
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from https://python.org
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist ".venv" python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
pip install pyinstaller

REM --collect-all pulls in the theme's .tcl files and the drag & drop
REM binaries, which PyInstaller cannot discover by reading the imports.
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name FotoRenamer ^
    --icon "assets\icon.ico" ^
    --collect-all sv_ttk ^
    --collect-all tkinterdnd2 ^
    app.py

if errorlevel 1 (
    echo.
    echo Build FAILED - scroll up for the reason.
    pause
    exit /b 1
)

echo.
echo Done.  Your app is here:  dist\FotoRenamer.exe
echo You can copy that single file anywhere - the desktop, a USB stick.
pause
