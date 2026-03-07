Set WshShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get desktop path
DesktopPath = WshShell.SpecialFolders("Desktop")
ProjectPath = "C:\Users\35456\true-learning-system"

' Create stop shortcut
Set oLink = WshShell.CreateShortcut(DesktopPath & "\TLS - Stop Server.lnk")
oLink.TargetPath = ProjectPath & "\stop_server.vbs"
oLink.WorkingDirectory = ProjectPath
oLink.IconLocation = "%SystemRoot%\System32\shell32.dll, 28"
oLink.Description = "Stop True Learning System"
oLink.Save

MsgBox "Stop shortcut created!", vbInformation, "Success"
