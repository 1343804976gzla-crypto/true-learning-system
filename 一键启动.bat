@echo off
setlocal

cd /d "C:\Users\35456\true-learning-system"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000"') do (
    taskkill /F /PID %%a >nul 2>&1
)

timeout /t 1 /nobreak >nul

if not defined TLS_RELOAD set TLS_RELOAD=0
start "" /B pythonw main.py

timeout /t 3 /nobreak >nul
start http://localhost:8000/wrong-answers

endlocal
exit
