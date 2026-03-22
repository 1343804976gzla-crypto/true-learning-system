@echo off
chcp 65001 >nul
setlocal

cd /d "C:\Users\35456\true-learning-system"

if /I "%TLS_FORCE_LOCAL%"=="1" goto local_mode

set "SHARED_SERVER_URL=http://localhost:18000/"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:18000/health' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel% equ 0 (
    start "" "%SHARED_SERVER_URL%"
    exit /b 0
)

:local_mode
set "SERVER_URL=http://localhost:8000/"
set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

netstat -ano | findstr ":8000" >nul
if %errorlevel% equ 0 (
    start "" "%SERVER_URL%"
    exit /b 0
)

start "True Learning System Server" /min cmd /c ""%PYTHON_EXE%" -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log"
timeout /t 4 /nobreak >nul
start "" "%SERVER_URL%"

exit /b 0
