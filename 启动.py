import subprocess
import time
import webbrowser
import os
import sys

print("=" * 60)
print("True Learning System - 启动中...")
print("=" * 60)

# 切换到项目目录
os.chdir(r'C:\Users\35456\true-learning-system')

# 清理端口冲突
print("\n[1/4] 正在清理端口冲突...")
subprocess.run('taskkill /F /IM pythonw.exe', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run('taskkill /F /IM python.exe', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 等待端口释放
time.sleep(2)

# 启动服务器
print("[2/4] 正在启动服务器...")
try:
    # 使用 CREATE_NO_WINDOW 标志隐藏窗口
    subprocess.Popen(
        [sys.executable, 'main.py'],
        cwd=r'C:\Users\35456\true-learning-system',
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )
    print("✅ 服务器启动成功")
except Exception as e:
    print(f"❌ 服务器启动失败: {e}")
    input("按回车键退出...")
    sys.exit(1)

# 等待服务器启动
print("[3/4] 等待服务器启动...")
for i in range(5, 0, -1):
    print(f"   {i} 秒...")
    time.sleep(1)

# 打开浏览器
print("[4/4] 正在打开浏览器...")
try:
    webbrowser.open('http://localhost:8000/wrong-answers')
    print("✅ 浏览器已打开")
except Exception as e:
    print(f"❌ 打开浏览器失败: {e}")
    print("请手动访问: http://localhost:8000/wrong-answers")

print("\n" + "=" * 60)
print("✅ 启动完成！")
print("=" * 60)
print("\n访问地址:")
print("  - 错题本: http://localhost:8000/wrong-answers")
print("  - 错题看板: http://localhost:8000/wrong-answers")
print("\n提示: 关闭此窗口不会停止服务器")
print("      如需停止服务器，请在任务管理器中结束 python.exe 进程")
print("\n")

# 等待用户查看信息
time.sleep(3)

