@echo off
REM ============================================================
REM Anti-Detect Browser - Network Diagnostic
REM ============================================================
echo.
echo  ===== Network Diagnostic =====
echo.
echo  [1] Windows Proxy Settings:
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable 2>nul
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer 2>nul
echo.
echo  [2] Testing DNS (google.com):
nslookup google.com 2>&1 | findstr "Address"
echo.
echo  [3] Testing connection to 8.8.8.8:
ping -n 2 8.8.8.8 2>&1 | findstr "Reply\|Request"
echo.
echo  [4] Hosts file (check for 127.0.0.1 redirects):
findstr /v "^#" %SystemRoot%\System32\drivers\etc\hosts | findstr /v "^$" 2>nul
echo.
echo  [5] Firefox profiles:
dir "%APPDATA%\Mozilla\Firefox\Profiles" 2>nul | findstr "DIR"
echo.
echo  ===== Done =====
echo  Is output ka screenshot bhejo!
pause
