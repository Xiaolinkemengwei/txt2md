@echo off
rem txt2md - launch the Fluent Design GUI (double-click to use)
setlocal
cd /d "%~dp0"

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Installing GUI dependencies, please wait...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Install failed. Install manually:
        echo     python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

where pythonw >nul 2>nul
if errorlevel 1 (
    python "%~dp0txt2md.py" --gui
) else (
    start "" pythonw "%~dp0txt2md.py" --gui
)
exit /b 0
