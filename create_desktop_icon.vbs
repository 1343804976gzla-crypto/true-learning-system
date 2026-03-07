Set WshShell = CreateObject("WScript.Shell")
DesktopPath = WshShell.SpecialFolders("Desktop")
ProjectPath = "C:\Users\35456\true-learning-system"

Set oLink = WshShell.CreateShortcut(DesktopPath & "\True Learning System.lnk")
oLink.TargetPath = ProjectPath & "\launcher.vbs"
oLink.WorkingDirectory = ProjectPath
oLink.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink.Description = "True Learning System - One Click Start"
oLink.Save
