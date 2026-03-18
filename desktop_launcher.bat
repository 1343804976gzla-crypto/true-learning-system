@echo off
chcp 65001 >nul
setlocal

cd /d "C:\Users\35456\true-learning-system"

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
