@echo off
chcp 936 >nul
title True Learning System
echo ==========================================
echo    True Learning System
echo    启动中...
echo ==========================================
echo.

cd /d "C:\Users\35456\true-learning-system"

REM Check Python
echo [1/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [Error] Python not found
    pause
    exit /b 1
)

REM Activate venv if exists
if exist "venv\Scripts\activate.bat" (
    echo [2/3] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [2/3] Using system Python...
)

echo [3/3] Starting server...
echo.
echo    Please wait...
echo    Browser will open automatically
echo    URL: http://localhost:8000
echo.

REM Open browser after 5 seconds
start /min "" cmd /c "timeout /t 5 /nobreak >nul && start "" "http://localhost:8000"

echo ==========================================
echo    Server is running
echo    Press Ctrl+C then Y to stop
echo ==========================================
echo.

REM Start server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log

echo.
echo Server stopped
timeout /t 2 >nul
