$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\True Learning System.lnk")
$Shortcut.TargetPath = "C:\Users\35456\true-learning-system\启动服务器.bat"
$Shortcut.WorkingDirectory = "C:\Users\35456\true-learning-system"
$Shortcut.Description = "True Learning System - 医学考研智能学习系统"
$Shortcut.IconLocation = "C:\Windows\System32\shell32.dll,13"
$Shortcut.Save()

Write-Host "✅ 桌面快捷方式创建成功！" -ForegroundColor Green
