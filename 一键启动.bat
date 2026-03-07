@echo off
chcp 65001 >nul

REM 切换到项目目录
cd /d "C:\Users\35456\true-learning-system"

REM 清理端口冲突
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000"') do (
    taskkill /F /PID %%a >nul 2>&1
)

REM 等待端口释放
timeout /t 1 /nobreak >nul

REM 启动服务器（后台运行）
start /B pythonw main.py

REM 等待服务器启动
timeout /t 3 /nobreak >nul

REM 打开浏览器
start http://localhost:8000/wrong-answers

REM 退出脚本（不显示窗口）
exit
