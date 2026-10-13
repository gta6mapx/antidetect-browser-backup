@echo off
REM ============================================================
REM Anti-Detect Browser - Chalao (dashboard start)
REM ============================================================
setlocal
cd /d "%~dp0\.."

if not exist .venv (
    echo  Pehle pc\setup.bat chalao - one-time setup.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
set PYTHONPATH=%CD%\vendor
where node >nul 2>&1
if not errorlevel 1 set PLAYWRIGHT_NODEJS_PATH=node

echo.
echo  Dashboard start ho raha hai: http://127.0.0.1:8765
echo  Band karne ke liye Ctrl+C dabao.
echo.
python -m src.gui.server
pause
