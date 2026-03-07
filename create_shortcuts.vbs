Set WshShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get desktop path
DesktopPath = WshShell.SpecialFolders("Desktop")
ProjectPath = "C:\Users\35456\true-learning-system"

' Create startup shortcut
Set oLink = WshShell.CreateShortcut(DesktopPath & "\True Learning System.lnk")
oLink.TargetPath = ProjectPath & "\start_server.vbs"
oLink.WorkingDirectory = ProjectPath
oLink.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink.Description = "Start True Learning System"
oLink.Save

' Create browser shortcut
Set oLink2 = WshShell.CreateShortcut(DesktopPath & "\TLS - Open Browser.lnk")
oLink2.TargetPath = "http://localhost:8000"
oLink2.IconLocation = "%SystemRoot%\System32\shell32.dll, 14"
oLink2.Description = "Open True Learning System in Browser"
oLink2.Save

MsgBox "Shortcuts created successfully!" & vbCrLf & vbCrLf & "Created:" & vbCrLf & "1. True Learning System (Start Server)" & vbCrLf & "2. TLS - Open Browser", vbInformation, "Success"
