Set objWMIService = GetObject("winmgmts://./root/cimv2")
Set WshShell = CreateObject("WScript.Shell")

' 查找并结束Python进程
Set colProcesses = objWMIService.ExecQuery("Select * from Win32_Process Where Name = 'python.exe' AND CommandLine LIKE '%main.py%'")

If colProcesses.Count = 0 Then
    MsgBox "True Learning System 没有在运行", vbInformation, "提示"
Else
    For Each objProcess in colProcesses
        objProcess.Terminate()
    Next
    MsgBox "True Learning System 已停止", vbInformation, "已停止"
End If
