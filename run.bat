@echo off
REM Run Foto Renamer straight from the Python source (no build needed).
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
    echo First run: creating a private Python environment...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Setup FAILED - scroll up for the reason.
        echo A missing internet connection is the usual cause.
        pause
        exit /b 1
    )
) else (
    call ".venv\Scripts\activate.bat"
)

python app.py

REM Without this, a start-up error would flash past and the window would
REM close before anyone could read it.
if errorlevel 1 (
    echo.
    echo Foto Renamer stopped with an error - the reason is just above.
    pause
    exit /b 1
)
