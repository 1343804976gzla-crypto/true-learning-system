$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\True Learning System.lnk")
$Shortcut.TargetPath = "C:\Users\35456\true-learning-system\start_system.bat"
$Shortcut.WorkingDirectory = "C:\Users\35456\true-learning-system"
$Shortcut.IconLocation = "%SystemRoot%\System32\shell32.dll,14"
$Shortcut.Description = "True Learning System"
$Shortcut.Save()
Write-Host "快捷方式已创建到桌面"
