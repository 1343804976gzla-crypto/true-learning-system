@echo off
setlocal
title True Learning System Server

cd /d "C:\Users\35456\true-learning-system"

echo [1/3] Checking port 8000...
netstat -ano | findstr ":8000" >nul
if %errorlevel% equ 0 (
    echo Port 8000 is in use. Stopping existing process...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 2 /nobreak >nul
) else (
    echo Port 8000 is available.
)

if not defined TLS_RELOAD set TLS_RELOAD=0
if /I "%TLS_RELOAD%"=="1" (
    echo [2/3] Starting server in hot reload mode...
) else (
    echo [2/3] Starting server in stable mode...
)
start "" /B python main.py

echo [3/3] Waiting for server startup...
timeout /t 3 /nobreak >nul

start http://localhost:8000/wrong-answers

echo.
echo Server started: http://localhost:8000/wrong-answers
echo Set TLS_RELOAD=1 before running this script if you need hot reload.
pause
endlocal
