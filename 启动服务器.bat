@echo off
chcp 65001 >nul
title True Learning System - 服务器

echo ========================================
echo   True Learning System
echo   医学考研智能学习系统
echo ========================================
echo.

cd /d "C:\Users\35456\true-learning-system"

echo [1/3] 检查端口占用...
netstat -ano | findstr ":8000" >nul
if %errorlevel% equ 0 (
    echo ⚠️  端口 8000 已被占用，正在清理...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 2 /nobreak >nul
    echo ✅ 端口清理完成
) else (
    echo ✅ 端口 8000 可用
)

echo.
echo [2/3] 启动服务器...
echo 📍 项目路径: %cd%
echo 🌐 访问地址: http://localhost:8000
echo.

start /B python main.py

echo.
echo [3/3] 等待服务器启动...
timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   ✅ 服务器启动成功！
echo ========================================
echo.
echo 📌 快速访问:
echo    - 错题本: http://localhost:8000/wrong-answers
echo    - 数据看板: http://localhost:8000/dashboard/stats
echo.
echo 💡 提示:
echo    - 按 Ctrl+C 停止服务器
echo    - 关闭此窗口也会停止服务器
echo.

start http://localhost:8000/wrong-answers

pause
