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

REM A half-created .venv - the usual result of closing this window during the
REM first run - has no activate.bat. Start over rather than fail confusingly.
if exist ".venv" if not exist ".venv\Scripts\activate.bat" (
    echo The Python environment is incomplete; rebuilding it...
    rmdir /s /q ".venv"
)

if not exist ".venv" (
    echo First run: creating a private Python environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create the environment - scroll up for the reason.
        pause
        exit /b 1
    )
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
    REM Remember what we installed, so an updated requirements.txt is noticed.
    copy /y "requirements.txt" ".venv\installed-requirements.txt" >nul
) else (
    call ".venv\Scripts\activate.bat"
    fc /b "requirements.txt" ".venv\installed-requirements.txt" >nul 2>nul
    if errorlevel 1 (
        echo Dependencies changed since the last run; updating...
        pip install -r requirements.txt
        if errorlevel 1 (
            echo.
            echo Update FAILED - scroll up for the reason.
            pause
            exit /b 1
        )
        copy /y "requirements.txt" ".venv\installed-requirements.txt" >nul
    )
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
