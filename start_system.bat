@echo off
setlocal
title True Learning System

echo ==========================================
echo    True Learning System
echo ==========================================
echo.

cd /d "C:\Users\35456\true-learning-system"

if /I "%TLS_FORCE_LOCAL%"=="1" goto local_mode

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:18000/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel% equ 0 (
    echo [Info] Docker shared instance detected on http://localhost:18000
    echo [Info] Opening shared instance to avoid split local data...
    start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 1; Start-Process 'http://localhost:18000'"
    exit /b 0
)

:local_mode
echo [1/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [Error] Python not found
    pause
    exit /b 1
)

if exist "venv\Scripts\activate.bat" (
    echo [2/3] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [2/3] Using system Python...
)

echo [3/3] Starting server...
echo.
echo    URL: http://localhost:8000
echo.

start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 5; Start-Process 'http://localhost:8000'"

set RELOAD_FLAG=
if /I "%TLS_RELOAD%"=="1" (
    echo    Mode: hot reload
    set RELOAD_FLAG=--reload
) else (
    echo    Mode: stable run
)

python -m uvicorn main:app --host 0.0.0.0 --port 8000 %RELOAD_FLAG% --no-access-log

echo.
echo Server stopped
timeout /t 2 >nul
endlocal
