@echo off
REM ============================================================
REM Anti-Detect Browser - Windows Setup (one-time)
REM Ye script: Python check -> venv -> deps -> browsers -> ready
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0\.."

echo.
echo  [1/5] Python check kar rahe hain...
py --version >nul 2>&1
if errorlevel 1 (
    echo  Python nahi mila. Install ho raha hai...
    winget install Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo  ERROR: Python install nahi hua. https://www.python.org/downloads/ se manually install karein.
        pause
        exit /b 1
    )
    echo  Python install ho gaya. Script dobara chalayein.
    pause
    exit /b 0
)
py --version

echo.
echo  [2/5] Virtual environment bana rahe hain...
if not exist ".venv\Scripts\activate.bat" (
    if exist ".venv" rmdir /s /q ".venv"
    py -m venv .venv
)
call .venv\Scripts\activate.bat

echo.
echo  [3/5] Dependencies install ho rahi hain (thoda time lagega)...
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if errorlevel 1 (
    echo  ERROR: pip install fail hua.
    pause
    exit /b 1
)

echo.
echo  [4/5] Browsers download ho rahe hain (Camoufox + Chromium)...
python setup_browser.py
echo  Fingerprint model download ho raha hai...
python -c "import sys; sys.path.insert(0, 'vendor'); from camoufox.fpgen_model import ensure_fpgen_model; ensure_fpgen_model(); print('fpgen model ready.')"
if errorlevel 1 (
    echo  WARNING: Camoufox download mein issue. Internet check karke dobara chalayein.
)

REM --- Patchright Chromium (Node chahiye driver ke liye) ---
where node >nul 2>&1
if errorlevel 1 (
    echo  Node.js nahi mila. Install ho raha hai...
    winget install OpenJS.NodeJS.LTS -e --silent --accept-source-agreements --accept-package-agreements
)
set PYTHONPATH=%CD%\vendor
set PLAYWRIGHT_NODEJS_PATH=node
python -m patchright install chromium
if errorlevel 1 (
    echo  WARNING: Chromium download mein issue. Baad mein dobara try karein:
    echo    set PYTHONPATH=%CD%\vendor ^&^& .venv\Scripts\python -m patchright install chromium
)

echo.
echo  [5/5] Setup complete!
echo.
echo  ============================================================
echo   Chalane ke liye:  pc\run.bat
echo   Dashboard:        http://127.0.0.1:8765
echo   Pehli dafa login: admin user auto-banega, password console
echo   pe print hoga (run.bat chalane par).
echo  ============================================================
echo.
pause
