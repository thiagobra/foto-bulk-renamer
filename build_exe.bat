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

if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create the build environment - scroll up for the reason.
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo The Python environment is broken. Delete the .venv folder and retry.
    pause
    exit /b 1
)
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo Could not install the dependencies - scroll up for the reason.
    pause
    exit /b 1
)
pip install "pyinstaller>=6.0"
if errorlevel 1 (
    echo Could not install PyInstaller - scroll up for the reason.
    pause
    exit /b 1
)

REM --collect-all pulls in the theme's .tcl files, the drag & drop binaries
REM and the HEIC decoder, none of which PyInstaller can find by reading the
REM imports.  --add-data ships assets\ so the app can set its own window icon
REM at run time (--icon only decorates the .exe file in Explorer).
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name FotoRenamer ^
    --icon "assets\icon.ico" ^
    --add-data "assets;assets" ^
    --collect-all sv_ttk ^
    --collect-all tkinterdnd2 ^
    --collect-all pillow_heif ^
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
echo.
echo If it does not start when you double-click it, run this to see why:
echo     dist\FotoRenamer.exe
echo (the build is --windowed, so errors are not printed on their own)
pause
