Set WshShell = CreateObject("WScript.Shell")

' 切换到项目目录
WshShell.CurrentDirectory = "C:\Users\35456\true-learning-system"

' 清理端口冲突
WshShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -ano ^| findstr "":8000""') do taskkill /F /PID %a", 0, True

' 等待1秒
WScript.Sleep 1000

' 启动服务器（隐藏窗口）
WshShell.Run "pythonw main.py", 0, False

' 等待3秒让服务器启动
WScript.Sleep 3000

' 打开浏览器
WshShell.Run "http://localhost:8000/wrong-answers"
