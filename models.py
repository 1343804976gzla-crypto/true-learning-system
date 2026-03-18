"""
数据库模型 - SQLAlchemy ORM
True Learning System - Core Models
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Date, DateTime, 
    Float, Boolean, ForeignKey, JSON, CheckConstraint, PrimaryKeyConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, date
import os
from pathlib import Path
from dotenv import load_dotenv

# 数据库路径
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _resolve_database_url() -> str:
    """Resolve DATABASE_PATH from env and normalize to a sqlite URL."""
    db_setting = (os.getenv("DATABASE_PATH") or "").strip()

    if db_setting.startswith("sqlite:///"):
        return db_setting

    if db_setting:
        db_path = Path(db_setting)
        if not db_path.is_absolute():
            db_path = (BASE_DIR / db_path).resolve()
    else:
        db_path = (BASE_DIR / "data" / "learning.db").resolve()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


DATABASE_URL = _resolve_database_url()

# 创建引擎
engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False},
    echo=False  # 生产环境设为False
)

# 会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 基类
Base = declarative_base()


class DailyUpload(Base):
    """日期轨道: 原始上传记录"""
    __tablename__ = "daily_uploads"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    date = Column(Date, nullable=False, index=True)
    raw_content = Column(Text, nullable=False)  # 原始讲课内容
    ai_extracted = Column(JSON)  # AI识别结果
    created_at = Column(DateTime, default=datetime.now)


class Chapter(Base):
    """章节轨道: 结构化知识"""
    __tablename__ = "chapters"
    
    id = Column(String, primary_key=True, index=True)  # 如: "medicine_ch2-1"
    book = Column(String, nullable=False, index=True)  # 书名: "内科学"
    edition = Column(String)  # 版本: "第10版"
    chapter_number = Column(String, nullable=False)  # 章节号: "2-1"
    chapter_title = Column(String, nullable=False)  # 章节标题: "心力衰竭"
    content_summary = Column(Text)  # AI生成的摘要
    concepts = Column(JSON)  # 知识点列表 [{"id": "...", "name": "..."}]
    first_uploaded = Column(Date)
    last_reviewed = Column(Date)
    
    # 关联
    concept_mastery_records = relationship("ConceptMastery", back_populates="chapter")


class ConceptMastery(Base):
    """知识点掌握状态"""
    __tablename__ = "concept_mastery"
    
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    concept_id = Column(String, primary_key=True, index=True)  # 如: "medicine_ch2-1_hf_def"
    chapter_id = Column(String, ForeignKey("chapters.id"), nullable=False)
    name = Column(String, nullable=False)  # 知识点名称
    retention = Column(Float, default=0.0)  # 记忆保留 0-1
    understanding = Column(Float, default=0.0)  # 理解深度 0-1
    application = Column(Float, default=0.0)  # 应用能力 0-1
    last_tested = Column(Date)
    next_review = Column(Date, index=True)  # FSRS计算的下次复习
    
    # 关联
    chapter = relationship("Chapter", back_populates="concept_mastery_records")
    test_records = relationship("TestRecord", back_populates="concept")
    wrong_answers = relationship("WrongAnswer", back_populates="concept")


class TestRecord(Base):
    """测试记录 (AI出题 + 批改)"""
    __tablename__ = "test_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    concept_id = Column(String, ForeignKey("concept_mastery.concept_id"))
    test_type = Column(String)  # 'ai_quiz', 'feynman', 'variation'
    
    # AI生成的题目
    ai_question = Column(Text)
    ai_options = Column(JSON)  # {A: "...", B: "...", C: "...", D: "..."}
    ai_correct_answer = Column(String)  # "A", "B", "C", or "D"
    ai_explanation = Column(Text)
    
    # 用户回答
    user_answer = Column(String)
    confidence = Column(String)  # 'sure', 'unsure', 'no'
    
    # AI批改结果
    is_correct = Column(Boolean)
    ai_feedback = Column(Text)
    weak_points = Column(JSON)  # ["薄弱点1", "薄弱点2"]
    
    score = Column(Integer)  # 0-100
    tested_at = Column(DateTime, default=datetime.now)
    
    # 关联
    concept = relationship("ConceptMastery", back_populates="test_records")


class FeynmanSession(Base):
    """费曼讲解对话记录"""
    __tablename__ = "feynman_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(String, nullable=False)
    concept_name = Column(String)
    dialogue = Column(JSON)  # [{"role": "user/assistant", "content": "...", "time": "..."}]
    passed = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime)


class ConceptLink(Base):
    """知识图谱连接"""
    __tablename__ = "concept_links"
    
    from_concept = Column(String, nullable=False)
    to_concept = Column(String, nullable=False)
    link_type = Column(String)  # 'prerequisite', 'leads_to', 'contrast', 'analogy'
    strength = Column(Float, default=1.0)
    user_created = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    
    __table_args__ = (
        PrimaryKeyConstraint('from_concept', 'to_concept'),
    )


class Variation(Base):
    """变式题库"""
    __tablename__ = "variations"
    
    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(String, nullable=False, index=True)
    level = Column(Integer)  # 1, 2, 3
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    explanation = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class WrongAnswer(Base):
    """错题本 - 记录用户答错的题目"""
    __tablename__ = "wrong_answers"
    
    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(String, ForeignKey("concept_mastery.concept_id"), nullable=False)
    
    # 题目内容（完整记录，即使原题被删除也能复习）
    question = Column(Text, nullable=False)
    options = Column(JSON)  # {"A": "选项A", "B": "选项B", ...}
    correct_answer = Column(String, nullable=False)  # "A"
    user_answer = Column(String, nullable=False)  # "B"
    explanation = Column(Text)  # 解析
    
    # 错误分析
    error_type = Column(String)  # 'knowledge_gap'(知识漏洞), 'misunderstanding'(理解错误), 'careless'(粗心), 'unknown'(未知)
    weak_points = Column(JSON)  # ["薄弱点1", "薄弱点2"]
    
    # 复习状态
    review_count = Column(Integer, default=0)  # 复习次数
    last_reviewed = Column(DateTime)  # 上次复习时间
    next_review = Column(Date, default=date.today)  # 下次复习时间
    mastery_level = Column(Integer, default=0)  # 掌握程度 0-5
    is_mastered = Column(Boolean, default=False)  # 是否已掌握
    
    # 关联
    concept = relationship("ConceptMastery", back_populates="wrong_answers")
    
    created_at = Column(DateTime, default=datetime.now)


class QuizSession(Base):
    """测验会话 - 记录一次10道题练习的完整信息"""
    __tablename__ = "quiz_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    session_type = Column(String, default="practice")  # 'practice'(正常练习), 'wrong_answer_review'(错题复习), 'chapter_test'(章节测试)
    
    # 如果是章节测试
    chapter_id = Column(String, ForeignKey("chapters.id"))
    
    # 题目列表（10道题）
    questions = Column(JSON)  # [{"question_id": "...", "concept_id": "...", "question": "...", "options": {...}, "correct_answer": "...", "explanation": "..."}]
    
    # 答题记录
    answers = Column(JSON)  # [{"question_index": 0, "user_answer": "A", "is_correct": true, "time_spent": 30}]
    
    # 统计
    total_questions = Column(Integer, default=10)
    correct_count = Column(Integer, default=0)
    score = Column(Integer, default=0)  # 0-100
    
    # 时间
    started_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime)
    
    # 关联
    chapter = relationship("Chapter")


# 数据库依赖
def get_db():
    """FastAPI依赖注入用"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库 (创建所有表)"""
    Base.metadata.create_all(bind=engine)
    print("✅ 数据库初始化完成")


if __name__ == "__main__":
    init_db()
