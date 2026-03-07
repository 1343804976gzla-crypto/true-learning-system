"""
初始化学习轨迹记录数据库
"""
from learning_tracking_models import create_learning_tracking_tables

if __name__ == "__main__":
    create_learning_tracking_tables()
    print("✅ 学习轨迹记录系统初始化完成！")
