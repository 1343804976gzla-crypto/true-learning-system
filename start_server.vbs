Set WshShell = CreateObject("WScript.Shell")
Set objWMIService = GetObject("winmgmts://./root/cimv2")

ProjectPath = "C:\Users\35456\true-learning-system"

' 检查是否已在运行
Set colProcesses = objWMIService.ExecQuery("Select * from Win32_Process Where Name = 'python.exe' AND CommandLine LIKE '%main.py%'")
If colProcesses.Count > 0 Then
    result = MsgBox("True Learning System 已经在运行！" & vbCrLf & vbCrLf & "是否要打开浏览器？", vbYesNo + vbQuestion, "服务已运行")
    If result = vbYes Then
        WshShell.Run "http://localhost:8000", 1, False
    End If
    WScript.Quit
End If

' 启动服务器
WshShell.CurrentDirectory = ProjectPath
WshShell.Run "cmd /c python main.py", 0, False

' 等待服务器启动
WScript.Sleep 3000

' 检查服务状态
Dim http
On Error Resume Next
Set http = CreateObject("MSXML2.XMLHTTP")
http.Open "GET", "http://localhost:8000/health", False
http.Send
On Error GoTo 0

If http.Status = 200 Then
    result = MsgBox("True Learning System 启动成功！" & vbCrLf & vbCrLf & "是否要打开浏览器？", vbYesNo + vbInformation, "启动成功")
    If result = vbYes Then
        WshShell.Run "http://localhost:8000", 1, False
    End If
Else
    MsgBox "服务启动中，请稍后再试" & vbCrLf & vbCrLf & "3秒后会自动检查", vbInformation, "请稍候"
    WScript.Sleep 3000
    WshShell.Run "http://localhost:8000", 1, False
End If
