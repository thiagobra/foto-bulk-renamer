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
) else (
    call ".venv\Scripts\activate.bat"
)

python app.py
