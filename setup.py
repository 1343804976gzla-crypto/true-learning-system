"""
初始化脚本 - 创建数据库和安装依赖
"""

import subprocess
import sys
import os

# 项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

def install_dependencies():
    """安装Python依赖"""
    print("📦 安装依赖...")
    
    dependencies = [
        "fastapi",
        "uvicorn[standard]",
        "jinja2",
        "python-multipart",
        "openai",
        "aiofiles",
        "python-dotenv",
        "sqlalchemy",
        "pydantic"
    ]
    
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install"
        ] + dependencies)
        print("✅ 依赖安装完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 依赖安装失败: {e}")
        return False
    
    return True

def init_database():
    """初始化数据库"""
    print("🗄️ 初始化数据库...")
    
    try:
        from models import init_db
        init_db()
        print("✅ 数据库初始化完成")
        return True
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        return False

def create_env_file():
    """创建.env文件"""
    env_path = os.path.join(PROJECT_DIR, ".env")
    
    if os.path.exists(env_path):
        print("✅ .env文件已存在")
        return
    
    print("📝 创建.env文件...")
    
    with open(env_path, "w", encoding="utf-8") as f:
        f.write('# DeepSeek API Key (必填)\n')
        f.write('DEEPSEEK_API_KEY="your-deepseek-api-key-here"\n\n')
        f.write('# Gemini API Key (可选，仅重量级任务需要)\n')
        f.write('GEMINI_API_KEY="your-gemini-api-key-here"\n')
        f.write('GEMINI_BASE_URL="https://api.qingyuntop.top/v1"\n')
        f.write('GEMINI_MODEL="gemini-3-flash-preview"\n\n')
        f.write('# 数据库路径 (可选，默认使用data/learning.db)\n')
        f.write('DATABASE_PATH="./data/learning.db"\n')
    
    print("✅ .env文件创建完成")
    print("⚠️  请编辑.env文件，填入你的 API Key")

def main():
    """主函数"""
    print("=" * 50)
    print("True Learning System - 初始化")
    print("=" * 50)
    print()
    
    # 1. 安装依赖
    if not install_dependencies():
        print("\n❌ 初始化失败：依赖安装失败")
        return
    
    # 2. 创建.env文件
    create_env_file()
    
    # 3. 初始化数据库
    if not init_database():
        print("\n❌ 初始化失败：数据库创建失败")
        return
    
    print()
    print("=" * 50)
    print("✅ 初始化完成！")
    print("=" * 50)
    print()
    print("下一步：")
    print("1. 编辑 .env 文件，填入你的 DEEPSEEK_API_KEY（可选再填 GEMINI_API_KEY）")
    print("2. 运行: python main.py")
    print("3. 浏览器打开: http://localhost:8000")
    print()

if __name__ == "__main__":
    main()
