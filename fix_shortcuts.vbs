Set WshShell = CreateObject("WScript.Shell")
DesktopPath = WshShell.SpecialFolders("Desktop")
ProjectPath = "C:\Users\35456\true-learning-system"

Set oLink = WshShell.CreateShortcut(DesktopPath & "\Start True Learning System.lnk")
oLink.TargetPath = ProjectPath & "\start_server.vbs"
oLink.WorkingDirectory = ProjectPath
oLink.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink.Save

Set oLink2 = WshShell.CreateShortcut(DesktopPath & "\Open TLS Browser.lnk")
oLink2.TargetPath = "http://localhost:8000"
oLink2.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink2.Save

MsgBox "Shortcuts Created!", vbInformation, "Done"
