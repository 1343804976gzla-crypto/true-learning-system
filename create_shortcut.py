import os
import sys
import win32com.client

# 创建快捷方式
desktop = os.path.join(os.environ['USERPROFILE'], 'Desktop')
shortcut_path = os.path.join(desktop, 'True Learning System.lnk')

shell = win32com.client.Dispatch("WScript.Shell")
shortcut = shell.CreateShortCut(shortcut_path)

# 使用 pythonw.exe 启动器脚本（无窗口模式）
python_path = sys.executable.replace('python.exe', 'pythonw.exe')
launcher_path = r"C:\Users\35456\true-learning-system\启动器.py"

shortcut.TargetPath = python_path
shortcut.Arguments = f'"{launcher_path}"'
shortcut.WorkingDirectory = r"C:\Users\35456\true-learning-system"
shortcut.Description = "True Learning System - 医学考研智能学习系统"
shortcut.IconLocation = r"C:\Windows\System32\shell32.dll,13"
shortcut.save()

print("✅ 桌面快捷方式创建成功！")
print(f"📍 位置: {shortcut_path}")
print(f"🎯 目标: {python_path}")
print(f"📄 脚本: {launcher_path}")




