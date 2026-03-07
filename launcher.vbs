Set WshShell = CreateObject("WScript.Shell")
Set objWMIService = GetObject("winmgmts://./root/cimv2")

ProjectPath = "C:\Users\35456\true-learning-system"
ServerURL = "http://localhost:8000"

' Check if already running (兼容 main.py 与 uvicorn main:app 两种启动方式)
Set colProcesses = objWMIService.ExecQuery( _
    "Select * from Win32_Process Where Name = 'python.exe' AND (" & _
    "CommandLine LIKE '%main.py%' OR " & _
    "CommandLine LIKE '%main:app%' OR " & _
    "CommandLine LIKE '%uvicorn%main:app%')")

If colProcesses.Count > 0 Then
    ' Already running, just open browser
    WshShell.Run ServerURL, 1, False
    WScript.Quit
End If

' Not running, start server silently (window style 0 = hidden)
WshShell.CurrentDirectory = ProjectPath
WshShell.Run "cmd /c python -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log", 0, False

' Wait and check server health (retry up to 10 times)
Dim http
ServerReady = False

For i = 1 To 10
    WScript.Sleep 1500
    On Error Resume Next
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", ServerURL & "/health", False
    http.Send
    If Err.Number = 0 And http.Status = 200 Then
        ServerReady = True
        Exit For
    End If
    On Error GoTo 0
Next

' Open browser
WshShell.Run ServerURL, 1, False
