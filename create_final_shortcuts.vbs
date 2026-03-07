Set WshShell = CreateObject("WScript.Shell")
DesktopPath = WshShell.SpecialFolders("Desktop")
ProjectPath = "C:\Users\35456\true-learning-system"

' Create main shortcut
Set oLink = WshShell.CreateShortcut(DesktopPath & "\True Learning System.lnk")
oLink.TargetPath = ProjectPath & "\start_simple.vbs"
oLink.WorkingDirectory = ProjectPath
oLink.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink.Save

' Create browser shortcut  
Set oLink2 = WshShell.CreateShortcut(DesktopPath & "\Open TLS.lnk")
oLink2.TargetPath = "http://localhost:8000"
oLink2.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink2.Save

MsgBox "Shortcuts created on Desktop!", vbInformation, "Success"
