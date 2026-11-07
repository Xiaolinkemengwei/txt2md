@echo off
rem AI eye - launch the Fluent Design GUI (double-click to run).
rem NOTE: keep this file ASCII-only. cmd.exe reads .bat in the OEM code page
rem (cp936 on Chinese Windows), so UTF-8 Chinese here would show as garbage.
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
    python "%~dp0aieye.py" --gui
) else (
    start "" pythonw "%~dp0aieye.py" --gui
)
exit /b 0
