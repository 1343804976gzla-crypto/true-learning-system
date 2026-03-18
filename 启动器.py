import subprocess
import time
import webbrowser
import os
import sys
import psutil

print("=" * 60)
print("True Learning System - 启动中...")
print("=" * 60)

# 切换到项目目录
os.chdir(r'C:\Users\35456\true-learning-system')

# 清理端口冲突
print("\n[1/4] 正在清理端口冲突...")
for proc in psutil.process_iter(['pid', 'name']):
    try:
        if proc.info['name'] in ['python.exe', 'pythonw.exe']:
            # 检查是否是本项目的进程
            cmdline = proc.cmdline()
            if any(('main.py' in arg) or ('main:app' in arg) for arg in cmdline):
                print(f"   发现旧进程 PID {proc.info['pid']}，正在终止...")
                proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

# 等待端口释放
time.sleep(2)

# 启动服务器
print("[2/4] 正在启动服务器...")
try:
    # 使用 pythonw.exe 确保无窗口运行
    pythonw_path = sys.executable.replace('python.exe', 'pythonw.exe')

    # 使用 CREATE_NO_WINDOW 标志隐藏窗口
    # 使用 CREATE_NEW_PROCESS_GROUP 使进程独立运行
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    subprocess.Popen(
        [pythonw_path, '-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log'],
        cwd=r'C:\Users\35456\true-learning-system',
        stdout=open('server.log', 'w'),
        stderr=subprocess.STDOUT,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True  # 关闭所有文件描述符，完全独立
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
    webbrowser.open('http://localhost:8000/')
    print("✅ 浏览器已打开")
except Exception as e:
    print(f"❌ 打开浏览器失败: {e}")
    print("请手动访问: http://localhost:8000/")

print("\n" + "=" * 60)
print("✅ 启动完成！")
print("=" * 60)
print("\n访问地址:")
print("  - 首页: http://localhost:8000/")
print("  - 错题本: http://localhost:8000/wrong-answers")
print("  - 错题看板: http://localhost:8000/wrong-answers")
print("\n提示: 服务器正在后台运行")
print("      如需停止服务器，请在任务管理器中结束 python.exe 进程")
print("\n")

# 等待用户查看信息
time.sleep(3)
