Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\35456\true-learning-system"
WshShell.Run "python main.py", 0, False
WScript.Sleep 3000
WshShell.Run "http://localhost:8000", 1, False
