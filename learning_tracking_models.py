"""
学习轨迹记录系统 - 新增模型
用于记录详细的学习过程和轨迹
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Date, DateTime, 
    Float, Boolean, ForeignKey, JSON, Enum, Interval, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, date, timedelta
import enum
import hashlib

# 复用现有引擎和基类
from models import Base, engine, SessionLocal


INVALID_CHAPTER_IDS = {
    "",
    "0",
    "unknown_ch0",
    "未知_ch0",
    "无法识别_ch0",
    "未分类_ch0",
    "uncategorized_ch0",
}


def make_fingerprint(text: str) -> str:
    """生成题目指纹，用于去重"""
    return hashlib.md5(text.strip().encode('utf-8')).hexdigest()


class SessionStatus(str, enum.Enum):
    """会话状态"""
    IN_PROGRESS = "in_progress"  # 进行中
    COMPLETED = "completed"      # 已完成
    ABANDONED = "abandoned"      # 已放弃
    PAUSED = "paused"            # 已暂停


class ActivityType(str, enum.Enum):
    """活动类型"""
    EXAM_START = "exam_start"           # 开始整卷测试
    EXAM_SUBMIT = "exam_submit"         # 提交整卷
    EXAM_REVIEW = "exam_review"         # 查看整卷解析
    DETAIL_PRACTICE_START = "detail_practice_start"  # 开始细节练习
    DETAIL_PRACTICE_SUBMIT = "detail_practice_submit" # 提交细节练习
    CONFIDENCE_MARK = "confidence_mark" # 标记自信度
    QUESTION_ANSWER = "question_answer" # 回答题目
    QUESTION_SKIP = "question_skip"     # 跳过题目
    COPY_REPORT = "copy_report"         # 复制报告
    NAVIGATE = "navigate"               # 页面导航


class LearningSession(Base):
    """
    学习会话 - 记录一次完整的学习过程
    包含整卷测试或细节练习的完整轨迹
    """
    __tablename__ = "learning_sessions"
    
    id = Column(String, primary_key=True, index=True)  # UUID
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    session_type = Column(String, nullable=False)  # 'exam'(整卷), 'detail_practice'(细节练习)
    
    # 关联信息
    chapter_id = Column(String, nullable=True)  # 章节ID
    exam_id = Column(String, nullable=True)     # 关联的试卷ID
    
    # 基本信息
    title = Column(String)  # 会话标题，如"2026-02-19 整卷测试"
    description = Column(Text)  # 描述
    
    # 内容记录
    uploaded_content = Column(Text)  # 上传的讲课内容（可选）
    knowledge_point = Column(String)  # 细节练习时的知识点
    
    # 统计信息
    total_questions = Column(Integer, default=0)
    answered_questions = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    wrong_count = Column(Integer, default=0)
    score = Column(Integer, default=0)
    accuracy = Column(Float, default=0.0)  # 正确率 0-1
    
    # 自信度统计
    sure_count = Column(Integer, default=0)      # 确定
    unsure_count = Column(Integer, default=0)    # 模糊
    no_count = Column(Integer, default=0)        # 不会
    
    # 时间记录
    started_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, default=0)  # 总用时（秒）
    
    # 状态
    status = Column(String, default=SessionStatus.IN_PROGRESS)
    
    # 元数据
    user_agent = Column(String)  # 浏览器信息
    ip_address = Column(String)  # IP地址
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联 - 一对多
    activities = relationship("LearningActivity", back_populates="session", cascade="all, delete-orphan")
    question_records = relationship("QuestionRecord", back_populates="session", cascade="all, delete-orphan")


class LearningActivity(Base):
    """
    学习活动 - 记录学习过程中的每个动作
    高粒度的时间轴记录
    """
    __tablename__ = "learning_activities"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    session_id = Column(String, ForeignKey("learning_sessions.id"), nullable=False, index=True)
    
    # 活动信息
    activity_type = Column(String, nullable=False)  # ActivityType
    activity_name = Column(String)  # 人类可读的活动名称
    
    # 详细数据
    data = Column(JSON)  # 存储任意相关数据
    # 例如：
    # - question_answer: {"question_index": 1, "answer": "A", "time_spent": 30}
    # - confidence_mark: {"question_index": 1, "confidence": "sure"}
    # - exam_submit: {"score": 85, "correct_count": 8}
    
    # 时间戳
    timestamp = Column(DateTime, default=datetime.now)
    relative_time_ms = Column(Integer, default=0)  # 相对于会话开始的时间（毫秒）
    
    # 关联
    session = relationship("LearningSession", back_populates="activities")


class QuestionRecord(Base):
    """
    题目记录 - 记录每道题的详细答题过程
    """
    __tablename__ = "question_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    session_id = Column(String, ForeignKey("learning_sessions.id"), nullable=False, index=True)
    
    # 题目信息
    question_index = Column(Integer, nullable=False)  # 题号（0-based）
    question_type = Column(String)  # A1, A2, A3, X
    difficulty = Column(String)     # 基础, 提高, 难题
    
    # 题目内容快照
    question_text = Column(Text)
    options = Column(JSON)  # {A: "...", B: "...", ...}
    correct_answer = Column(String)
    explanation = Column(Text)
    key_point = Column(String)  # 知识点
    
    # 答题记录
    user_answer = Column(String)
    is_correct = Column(Boolean)
    confidence = Column(String)  # sure, unsure, no
    
    # 时间记录
    first_viewed_at = Column(DateTime)  # 首次查看
    answered_at = Column(DateTime)      # 回答时间
    time_spent_seconds = Column(Integer, default=0)  # 用时
    
    # 答题过程（如果有多步）
    answer_changes = Column(JSON)  # [{"from": "A", "to": "B", "at": "..."}]
    
    # 关联
    session = relationship("LearningSession", back_populates="question_records")


class DailyLearningLog(Base):
    """
    每日学习日志 - 汇总每天的学习情况
    """
    __tablename__ = "daily_learning_logs"
    __table_args__ = (
        UniqueConstraint("actor_key", "date", name="uq_daily_learning_logs_actor_date"),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    
    # 统计
    total_sessions = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    total_correct = Column(Integer, default=0)
    total_wrong = Column(Integer, default=0)
    average_score = Column(Float, default=0.0)
    
    # 时间
    total_duration_seconds = Column(Integer, default=0)
    first_session_at = Column(DateTime)
    last_session_at = Column(DateTime)
    
    # 知识点覆盖
    knowledge_points_covered = Column(JSON)  # ["知识点1", "知识点2"]
    weak_knowledge_points = Column(JSON)    # ["薄弱点1"]
    
    # 详细记录
    session_ids = Column(JSON)  # ["session_id_1", "session_id_2"]
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class LearningInsight(Base):
    """
    学习洞察 - AI分析的学习建议
    """
    __tablename__ = "learning_insights"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联
    session_id = Column(String, ForeignKey("learning_sessions.id"), nullable=True)
    date = Column(Date, nullable=True)
    
    # 洞察类型
    insight_type = Column(String)  # 'pattern'(模式), 'warning'(警告), 'suggestion'(建议), 'achievement'(成就)
    
    # 内容
    title = Column(String, nullable=False)
    description = Column(Text)
    
    # 相关数据
    related_data = Column(JSON)  # 支撑洞察的数据
    
    # 是否已读
    is_read = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.now)


class WrongAnswerV2(Base):
    """
    错题本V2 - 基于QuestionRecord自动收录
    按题目指纹去重，聚合统计，急救标签分级
    """
    __tablename__ = "wrong_answers_v2"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    question_fingerprint = Column(String(64), nullable=False, unique=True, index=True)

    # 题目快照
    question_text = Column(Text, nullable=False)
    options = Column(JSON)
    correct_answer = Column(String)
    explanation = Column(Text)
    key_point = Column(String, index=True)
    question_type = Column(String)       # A1/A2/A3/X
    difficulty = Column(String)          # 基础/提高/难题
    chapter_id = Column(String, ForeignKey("chapters.id"), nullable=True)

    # 聚合统计
    error_count = Column(Integer, default=1)
    encounter_count = Column(Integer, default=1)
    retry_count = Column(Integer, default=0)
    last_retry_correct = Column(Boolean, nullable=True)
    last_retry_confidence = Column(String, nullable=True)

    # 急救标签: critical/stubborn/landmine/normal
    severity_tag = Column(String, default="normal", index=True)
    # 状态: active/archived
    mastery_status = Column(String, default="active", index=True)

    # 关联的QuestionRecord IDs
    linked_record_ids = Column(JSON, default=list)

    # SM-2 间隔重复字段
    sm2_ef = Column(Float, default=2.5)           # Easiness Factor (1.3~2.5)
    sm2_interval = Column(Integer, default=0)     # 当前间隔天数
    sm2_repetitions = Column(Integer, default=0)  # 连续正确次数
    next_review_date = Column(Date, nullable=True) # 下次复习日期

    # 变式数据（AI生成的变式题缓存）
    variant_data = Column(JSON, nullable=True)
    # 结构: {
    #   "variant_question": "变式题文本",
    #   "variant_options": {"A":"...", "B":"...", "C":"...", "D":"...", "E":"..."},
    #   "variant_answer": "C",
    #   "variant_explanation": "解析",
    #   "transform_type": "病例变式/选项重组/反向提问/...",
    #   "core_knowledge": "不变的核心考点",
    #   "generated_at": "ISO时间"
    # }

    # 时间
    first_wrong_at = Column(DateTime)
    last_wrong_at = Column(DateTime)
    last_retried_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # ========== 融合升级字段 (Merge & Upgrade) ==========
    # 支持无限层级融合：parent_ids 存储原题ID列表 [1, 2, 3]
    # 这些原题作为底层基石，形成知识图谱的图结构
    parent_ids = Column(JSON, nullable=True)

    # 标记是否为融合题（在UI上显示"🔥 升级挑战"徽章）
    is_fusion = Column(Boolean, default=False, index=True)

    # 融合层级：基础题=0，第一次融合=1，第二次融合=2...
    # 层级越高，惩罚系数越重（答错时复习间隔重置更快）
    fusion_level = Column(Integer, default=0, index=True)

    # 严格模式SM-2的惩罚系数（动态基于fusion_level）
    # L1=1.5, L2=2.0, L3+=2.5
    # 答错时：interval = max(MIN_INTERVAL, interval / penalty_factor)
    sm2_penalty_factor = Column(Float, default=1.0)

    # 融合题特有数据（AI评判结果、诊断记录等）
    fusion_data = Column(JSON, nullable=True)
    # 结构: {
    #   "expected_key_points": ["预期要点1", "预期要点2"],
    #   "scoring_criteria": {"逻辑严密性": 30, "概念准确性": 40, "综合应用": 30},
    #   "judgement_pending": true/false,  # 是否等待AI评判
    #   "user_answer_cache": "用户答案缓存",
    #   "diagnosis_history": [...]  # 诊断历史记录
    # }

    # 关联
    retries = relationship("WrongAnswerRetry", back_populates="wrong_answer", cascade="all, delete-orphan")


class WrongAnswerRetry(Base):
    """
    错题重做记录 - 每次重做产生一条
    """
    __tablename__ = "wrong_answer_retries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    wrong_answer_id = Column(Integer, ForeignKey("wrong_answers_v2.id"), nullable=False, index=True)
    user_answer = Column(String)
    is_correct = Column(Boolean)
    confidence = Column(String)
    time_spent_seconds = Column(Integer, default=0)
    retried_at = Column(DateTime, default=datetime.now)

    # 变式手术字段
    is_variant = Column(Boolean, default=False)       # 是否变式重做
    rationale_text = Column(Text, nullable=True)       # 用户逻辑自证文本
    ai_evaluation = Column(JSON, nullable=True)        # AI评估结果
    # 结构: {
    #   "verdict": "logic_closed/lucky_guess/failed",
    #   "reasoning_score": 0-100,
    #   "diagnosis": "AI诊断文本",
    #   "weak_links": ["薄弱环节1", "薄弱环节2"]
    # }

    # 地雷盲测字段
    is_landmine_recall = Column(Boolean, default=False)  # 是否地雷盲测重做

    # 关联
    wrong_answer = relationship("WrongAnswerV2", back_populates="retries")


class DailyReviewPaper(Base):
    """
    每日复习卷主表
    同一 actor 在同一天只保留一份抽题结果，确保重复导出时题卷稳定。
    """
    __tablename__ = "daily_review_papers"
    __table_args__ = (
        UniqueConstraint("actor_key", "paper_date", name="uq_daily_review_papers_actor_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)
    paper_date = Column(Date, nullable=False, index=True)
    total_questions = Column(Integer, default=0)
    config = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    items = relationship("DailyReviewPaperItem", back_populates="paper", cascade="all, delete-orphan")


class DailyReviewPaperItem(Base):
    """
    每日复习卷题目快照
    保存抽中题目的顺序与快照，便于后续稳定复现 PDF 内容与 5 天避重。
    """
    __tablename__ = "daily_review_paper_items"
    __table_args__ = (
        UniqueConstraint("paper_id", "position", name="uq_daily_review_paper_item_position"),
        UniqueConstraint("paper_id", "wrong_answer_id", name="uq_daily_review_paper_item_wrong_answer"),
    )

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(Integer, ForeignKey("daily_review_papers.id"), nullable=False, index=True)
    wrong_answer_id = Column(Integer, ForeignKey("wrong_answers_v2.id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    stem_fingerprint = Column(String(64), nullable=False, index=True)
    source_bucket = Column(String, default="due")
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    paper = relationship("DailyReviewPaper", back_populates="items")


class BatchExamState(Base):
    """
    鎵归噺璇曞嵎鐢熸垚鐘舵€佸揩鐓э紝鐢ㄤ簬鐢熸垚鍚庡埌鎻愪氦鍓嶇殑鎸佷箙鍖栦笌 actor 闅旂
    """
    __tablename__ = "batch_exam_states"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    device_id = Column(String, nullable=True, index=True)
    actor_key = Column(String, nullable=False, index=True)
    chapter_id = Column(String, nullable=True)
    chapter_prediction = Column(JSON, nullable=True)
    questions = Column(JSON, nullable=False)
    num_questions = Column(Integer, default=10)
    uploaded_content = Column(Text, nullable=True)
    fuzzy_options = Column(JSON, nullable=True)
    exam_wrong_questions = Column(JSON, nullable=True)
    score = Column(Integer, nullable=True)
    wrong_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    submitted_at = Column(DateTime, nullable=True)


# 创建表的函数
def create_learning_tracking_tables():
    """创建学习轨迹记录相关的表"""
    Base.metadata.create_all(bind=engine, tables=[
        LearningSession.__table__,
        LearningActivity.__table__,
        QuestionRecord.__table__,
        DailyLearningLog.__table__,
        LearningInsight.__table__,
        WrongAnswerV2.__table__,
        WrongAnswerRetry.__table__,
        DailyReviewPaper.__table__,
        DailyReviewPaperItem.__table__,
        BatchExamState.__table__,
    ])
    print("✅ 学习轨迹记录表创建完成")


if __name__ == "__main__":
    create_learning_tracking_tables()
