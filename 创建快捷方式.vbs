Set WshShell = CreateObject("WScript.Shell")
Set oShellLink = WshShell.CreateShortcut("C:\Users\35456\Desktop\True Learning System.lnk")

oShellLink.TargetPath = "C:\Users\35456\true-learning-system\启动服务器.bat"
oShellLink.WorkingDirectory = "C:\Users\35456\true-learning-system"
oShellLink.Description = "True Learning System - 医学考研智能学习系统"
oShellLink.IconLocation = "C:\Windows\System32\shell32.dll,13"

oShellLink.Save

WScript.Echo "✅ 桌面快捷方式创建成功！"
