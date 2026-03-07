"""
Pydantic数据验证模型
用于API请求/响应的数据验证和序列化
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Literal
from datetime import date, datetime


# ============================================
# 上传相关
# ============================================

class ContentUpload(BaseModel):
    """上传讲课内容请求"""
    content: str = Field(..., min_length=10, description="讲课内容文本")
    date: Optional[str] = Field(default=None, description="学习日期 (YYYY-MM-DD)")


class AIExtractedConcept(BaseModel):
    """AI识别的单个知识点"""
    id: str = Field(..., description="知识点ID")
    name: str = Field(..., description="知识点名称")
    importance: Optional[str] = Field(None, description="重要性: main/secondary/mention")
    evidence: Optional[str] = Field(None, description="判断依据")


class AIExtractedResult(BaseModel):
    """AI识别结果"""
    book: str = Field(..., description="书名")
    edition: Optional[str] = Field(None, description="版本")
    chapter_number: str = Field(..., description="章节号")
    chapter_title: str = Field(..., description="章节标题")
    chapter_id: Optional[str] = Field(None, description="章节ID")
    concepts: List[AIExtractedConcept] = Field(default_factory=list, description="知识点列表")
    summary: Optional[str] = Field(None, description="章节摘要")
    main_topic: Optional[str] = Field(None, description="主体内容概括")
    mentioned_topics: Optional[str] = Field(None, description="提及的内容")
    is_new_chapter: Optional[str] = Field(None, description="是否新章节")
    matched_existing: Optional[str] = Field(None, description="是否匹配已有知识")


class UploadResponse(BaseModel):
    """上传响应"""
    upload_id: int
    date: date
    extracted: AIExtractedResult
    message: str


# ============================================
# 章节相关
# ============================================

class ChapterInfo(BaseModel):
    """章节信息"""
    id: str
    book: str
    edition: Optional[str]
    chapter_number: str
    chapter_title: str
    content_summary: Optional[str]
    concept_count: int
    first_uploaded: Optional[date]
    last_reviewed: Optional[date]
    
    class Config:
        from_attributes = True


class ConceptInfo(BaseModel):
    """知识点信息"""
    concept_id: str
    chapter_id: str
    name: str
    retention: float = Field(0.0, ge=0.0, le=1.0)
    understanding: float = Field(0.0, ge=0.0, le=1.0)
    application: float = Field(0.0, ge=0.0, le=1.0)
    last_tested: Optional[date]
    next_review: Optional[date]
    
    class Config:
        from_attributes = True


class ChapterDetail(BaseModel):
    """章节详情"""
    chapter: ChapterInfo
    concepts: List[ConceptInfo]


# ============================================
# 测试相关
# ============================================

class QuizOption(BaseModel):
    """选择题选项"""
    A: str
    B: str
    C: str
    D: str


class GeneratedQuiz(BaseModel):
    """AI生成的题目"""
    id: int  # 测试记录ID
    concept_id: str
    concept_name: str
    question: str
    options: QuizOption
    
    # 正确答案和解析不返回给用户（测试后才显示）


class QuizSubmission(BaseModel):
    """提交答案请求"""
    test_id: int
    user_answer: Literal["A", "B", "C", "D"]
    confidence: Literal["sure", "unsure", "dont_know"]


class QuizResult(BaseModel):
    """测试批改结果"""
    test_id: int
    concept_id: str
    concept_name: str
    
    # 题目信息
    question: str
    options: QuizOption
    correct_answer: str
    ai_explanation: str
    
    # 用户回答
    user_answer: str
    is_correct: bool
    confidence: str
    
    # AI反馈
    ai_feedback: str
    weak_points: List[str]
    score: int = Field(..., ge=0, le=100)
    
    # 建议
    suggestion: str
    next_review: Optional[date]


class QuizHistoryItem(BaseModel):
    """测试历史单项"""
    test_id: int
    concept_name: str
    test_type: str
    is_correct: Optional[bool]
    score: int
    confidence: str
    tested_at: datetime
    
    class Config:
        from_attributes = True


# ============================================
# 费曼讲解相关
# ============================================

class FeynmanStartRequest(BaseModel):
    """开始费曼讲解请求"""
    concept_id: str


class FeynmanStartResponse(BaseModel):
    """开始费曼讲解响应"""
    session_id: int
    concept_name: str
    ai_message: str


class FeynmanRespondRequest(BaseModel):
    """费曼讲解回复请求"""
    session_id: int
    message: str = Field(..., min_length=1)


class FeynmanRespondResponse(BaseModel):
    """费曼讲解回复响应"""
    session_id: int
    finished: bool
    passed: bool
    message: str  # AI回复或完成消息
    round: int
    terminology_detected: Optional[List[str]] = None


# ============================================
# 变式题相关
# ============================================

class VariationGenerateRequest(BaseModel):
    """生成变式题请求"""
    concept_id: str
    level: Literal[1, 2, 3] = Field(..., description="变式层级: 1=直接, 2=应用, 3=综合")


class VariationInfo(BaseModel):
    """变式题信息"""
    id: int
    concept_id: str
    level: int
    question: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class VariationDetail(BaseModel):
    """变式题详情（含答案）"""
    id: int
    concept_id: str
    concept_name: str
    level: int
    question: str
    answer: str
    explanation: str


# ============================================
# 知识图谱相关
# ============================================

class GraphNode(BaseModel):
    """图谱节点"""
    id: str
    name: str
    chapter: str
    mastery: float = Field(..., ge=0.0, le=1.0)
    radius: float


class GraphLink(BaseModel):
    """图谱连接"""
    source: str
    target: str
    type: str
    strength: float


class GraphData(BaseModel):
    """图谱数据"""
    nodes: List[GraphNode]
    links: List[GraphLink]


class CreateLinkRequest(BaseModel):
    """创建连接请求"""
    from_concept: str
    to_concept: str
    link_type: Literal["prerequisite", "leads_to", "contrast", "analogy"]


# ============================================
# 错题本和测验相关
# ============================================

class QuizAnswerRequest(BaseModel):
    """单题答案"""
    question_index: int
    user_answer: str
    time_spent: int = 0  # 答题用时（秒）
    confidence: str = "unsure"  # sure, unsure, dont_know


class QuizSubmitRequest(BaseModel):
    """提交测验请求"""
    answers: List[QuizAnswerRequest]


class QuizResponse(BaseModel):
    """测验响应"""
    session_id: int
    mode: str
    total_questions: int
    questions: List[dict]


class QuizResultResponse(BaseModel):
    """测验结果"""
    session_id: int
    score: int
    correct_count: int
    wrong_count: int
    answers: List[dict]


# ============================================
# 仪表盘相关
# ============================================

class DailyTask(BaseModel):
    """今日任务"""
    type: Literal["review", "new", "weak"]
    concept_id: str
    concept_name: str
    chapter_title: str
    priority: int
    reason: str


class DashboardStats(BaseModel):
    """仪表盘统计"""
    total_concepts: int
    mastered_concepts: int
    learning_concepts: int
    weak_concepts: int
    today_tasks: int
    reviewed_today: int
    streak_days: int


class DashboardResponse(BaseModel):
    """仪表盘响应"""
    stats: DashboardStats
    today_tasks: List[DailyTask]
    recent_tests: List[QuizHistoryItem]
