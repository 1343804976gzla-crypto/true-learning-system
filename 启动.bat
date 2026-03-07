@echo off
title True Learning System
color 0A

echo ========================================
echo   True Learning System
echo   医学考研智能学习系统
echo ========================================
echo.

cd /d "C:\Users\35456\true-learning-system"

echo [1/4] 清理端口冲突...
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/4] 启动服务器...
start /B python main.py > server.log 2>&1

echo [3/4] 等待服务器启动...
timeout /t 5 /nobreak >nul

echo [4/4] 正在打开浏览器...
start http://localhost:8000/wrong-answers

echo.
echo ========================================
echo   启动完成！
echo ========================================
echo.
echo 访问地址:
echo   - 错题本: http://localhost:8000/wrong-answers
echo   - 数据看板: http://localhost:8000/dashboard/stats
echo.
echo 提示:
echo   - 服务器正在后台运行
echo   - 可以关闭此窗口
echo   - 如需停止服务器，请在任务管理器中结束 python.exe 进程
echo.

pause
