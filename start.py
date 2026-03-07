import subprocess
import time
import webbrowser
import os

print("True Learning System - 启动中...")

# 切换到项目目录
os.chdir(r'C:\Users\35456\true-learning-system')

# 清理端口冲突
print("清理端口冲突...")
os.system('taskkill /F /IM pythonw.exe >nul 2>&1')
os.system('taskkill /F /IM python.exe >nul 2>&1')
time.sleep(2)

# 启动服务器
print("启动服务器...")
os.system('start /B pythonw main.py')
time.sleep(5)

# 打开浏览器
print("打开浏览器...")
webbrowser.open('http://localhost:8000/wrong-answers')

print("启动完成！")
