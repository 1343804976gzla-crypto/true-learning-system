@echo off
setlocal
title True Learning System
color 0A

echo ========================================
echo   True Learning System
echo ========================================
echo.

cd /d "C:\Users\35456\true-learning-system"

echo [1/4] Cleaning previous Python processes...
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

if not defined TLS_RELOAD set TLS_RELOAD=0
if /I "%TLS_RELOAD%"=="1" (
    echo [2/4] Starting server in hot reload mode...
) else (
    echo [2/4] Starting server in stable mode...
)
start "" /B cmd /c "python main.py > server.log 2>&1"

echo [3/4] Waiting for server startup...
timeout /t 5 /nobreak >nul

echo [4/4] Opening browser...
start http://localhost:8000/wrong-answers

echo.
echo Server started: http://localhost:8000/wrong-answers
echo Logs: server.log
echo Set TLS_RELOAD=1 before running this script if you need hot reload.
echo.
pause
endlocal
