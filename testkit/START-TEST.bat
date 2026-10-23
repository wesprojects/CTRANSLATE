@echo off
setlocal
rem CTRANSLATE test server for a home network (see README-TEST.txt).
rem Plain http on port 8080 for this PC and phones on the same Wi-Fi, invented test accounts
rem (data\demo-accounts.txt), and port 8081 on this PC alone for a Cloudflare quick tunnel.
title CTRANSLATE test server
cd /d "%~dp0"
set PYTHONUTF8=1
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY (
  echo Python 3 is not installed. Get it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)
echo.
echo   CTRANSLATE test server
echo   ----------------------
echo   This PC:  http://localhost:8080/admin    http://localhost:8080/speak
echo   Phones on the same Wi-Fi:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo             http://%%b:8080/listen
echo   Test accounts: Test Admin 111111, Test Speaker 222222 (all in README-TEST.txt)
echo   Close this window to stop the server.
echo.
start "" /b powershell -NoProfile -Command "Start-Sleep 3; Start-Process 'http://localhost:8080/admin'"
%PY% ..\server.py --http --port 8080 --tunnel-port 8081 --demo --data data
echo.
echo The server stopped (see above).
pause
