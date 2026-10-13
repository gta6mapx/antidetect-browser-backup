@echo off
REM ============================================================
REM Anti-Detect Browser - ONE CLICK UPDATE (private repo via Git)
REM Pehli dafa: Git install + GitHub login (browser mein 1 click).
REM Uske baad hamesha: 1 click = auto update.
REM Tumhara data (.venv, profiles, settings) mehfooz rahega.
REM ============================================================
setlocal
cd /d "%~dp0\.."

where git >nul 2>&1
if %ERRORLEVEL%==0 goto GIT_OK
goto GIT_MISSING

:GIT_MISSING
echo.
echo  Git install ho raha hai (sirf 1 dafa)...
winget install Git.Git -e --silent --accept-source-agreements --accept-package-agreements
echo.
echo  Git install ho gaya. Ab update.bat DOBARA chalao.
pause
exit /b 0

:GIT_OK
if exist ".git" goto DO_PULL
goto DO_CLONE

:DO_CLONE
echo.
echo  Pehli dafa setup: ab browser mein GitHub login khulega.
echo  Login karo aur Authorize dabao. Bas 1 dafa karna hai.
echo.
if exist "%TEMP%\adrepo" rmdir /s /q "%TEMP%\adrepo"
git clone https://github.com/protectvibe14/antidetect-browser.git "%TEMP%\adrepo"
if %ERRORLEVEL%==0 goto CLONE_OK
echo.
echo  ERROR: Clone fail hua. Internet / GitHub login check karo.
pause
exit /b 1

:CLONE_OK
echo  Naya code apply ho raha hai (tumhara data mehfooz rahega)...
powershell -NoProfile -Command "Get-ChildItem -Force '%TEMP%\adrepo' | Copy-Item -Destination '%CD%' -Recurse -Force"
rmdir /s /q "%TEMP%\adrepo"
goto REFRESH_DEPS

:DO_PULL
echo  Update download ho raha hai...
git fetch origin
if %ERRORLEVEL%==0 goto FETCH_OK
echo  ERROR: Update fail hua. Internet check karo.
pause
exit /b 1

:FETCH_OK
git reset --hard origin/main
goto REFRESH_DEPS

:REFRESH_DEPS
if not exist ".venv" goto DONE
call .venv\Scripts\activate.bat
pip install -r requirements.txt -q

:DONE
echo.
echo  ============================================================
echo   UPDATE COMPLETE! Ab pc\run.bat se chalao.
echo  ============================================================
echo.
pause
