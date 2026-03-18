"""
工具函数模块
通用辅助函数
"""

from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any
import json


def format_date(dt: Optional[date]) -> str:
    """格式化日期显示"""
    if not dt:
        return "从未"
    
    if dt == date.today():
        return "今天"
    elif dt == date.today() - timedelta(days=1):
        return "昨天"
    elif (date.today() - dt).days < 7:
        return f"{(date.today() - dt).days}天前"
    else:
        return dt.strftime("%Y-%m-%d")


def format_datetime(dt: Optional[datetime]) -> str:
    """格式化日期时间显示"""
    if not dt:
        return "从未"
    
    if dt.date() == date.today():
        return f"今天 {dt.strftime('%H:%M')}"
    elif dt.date() == date.today() - timedelta(days=1):
        return f"昨天 {dt.strftime('%H:%M')}"
    else:
        return dt.strftime("%Y-%m-%d %H:%M")


def calculate_mastery_level(retention: float, understanding: float, application: float) -> Dict[str, Any]:
    """
    计算综合掌握度等级
    
    Returns:
        {
            'level': 'mastered' | 'learning' | 'weak' | 'new',
            'label': '已掌握' | '学习中' | '薄弱' | '未学习',
            'color': 'green' | 'yellow' | 'red' | 'gray',
            'overall_score': 综合得分 0-100
        }
    """
    # 加权平均：理解40%，记忆30%，应用30%
    overall = understanding * 0.4 + retention * 0.3 + application * 0.3
    
    if overall >= 0.8:
        return {
            'level': 'mastered',
            'label': '已掌握',
            'color': 'green',
            'overall_score': int(overall * 100)
        }
    elif overall >= 0.5:
        return {
            'level': 'learning',
            'label': '学习中',
            'color': 'yellow',
            'overall_score': int(overall * 100)
        }
    elif overall > 0:
        return {
            'level': 'weak',
            'label': '薄弱',
            'color': 'red',
            'overall_score': int(overall * 100)
        }
    else:
        return {
            'level': 'new',
            'label': '未学习',
            'color': 'gray',
            'overall_score': 0
        }


def get_confidence_text(confidence: str) -> str:
    """获取信心度中文文本"""
    mapping = {
        'sure': '确定会',
        'unsure': '有点模糊',
        'no': '完全不会',
        'dont_know': '完全不会'
    }
    return mapping.get(confidence, confidence)


def get_confidence_color(confidence: str) -> str:
    """获取信心度颜色"""
    mapping = {
        'sure': 'green',
        'unsure': 'yellow',
        'no': 'red',
        'dont_know': 'red'
    }
    return mapping.get(confidence, 'gray')


def truncate_text(text: str, max_length: int = 100) -> str:
    """截断文本，添加省略号"""
    if len(text) <= max_length:
        return text
    return text[:max_length] + '...'


def safe_json_loads(data: Optional[str], default: Any = None) -> Any:
    """安全的JSON解析"""
    if not data:
        return default
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return default


def calculate_next_review(score: int, current_interval: int = 1) -> int:
    """
    简化的FSRS计算下次复习间隔（天数）
    
    Args:
        score: 测试得分 0-100
        current_interval: 当前间隔天数
    
    Returns:
        新的间隔天数
    """
    if score >= 90:
        # 优秀：间隔3倍
        return max(current_interval * 3, 3)
    elif score >= 70:
        # 良好：间隔2倍
        return max(current_interval * 2, 2)
    elif score >= 50:
        # 及格：间隔1.5倍
        return max(int(current_interval * 1.5), 1)
    else:
        # 不及格：明天复习
        return 1


def analyze_confidence_accuracy(is_correct: bool, confidence: str) -> Dict[str, str]:
    """
    分析信心度与正确性的匹配度
    
    Returns:
        {
            'type': 'mastered' | 'lucky' | 'blind_spot' | 'aware_weak',
            'diagnosis': 诊断结果,
            'suggestion': 建议
        }
    """
    if confidence == 'sure':
        if is_correct:
            return {
                'type': 'mastered',
                'diagnosis': '高信心 + 正确 = 真正掌握',
                'suggestion': '保持当前状态，按计划复习'
            }
        else:
            return {
                'type': 'blind_spot',
                'diagnosis': '高信心 + 错误 = 危险盲区',
                'suggestion': '⚠️ 这是最需要关注的问题！建议使用费曼讲解重新理解'
            }
    elif confidence == 'unsure':
        if is_correct:
            return {
                'type': 'lucky',
                'diagnosis': '低信心 + 正确 = 运气蒙对',
                'suggestion': '建议再做一题验证，或回顾相关知识点'
            }
        else:
            return {
                'type': 'aware_weak',
                'diagnosis': '低信心 + 错误 = 正常盲区',
                'suggestion': '继续学习该知识点，从基础概念开始'
            }
    else:  # no / dont_know
        return {
            'type': 'aware_weak',
            'diagnosis': '完全不会',
            'suggestion': '建议重新学习该知识点的基础内容'
        }


def generate_study_suggestion(weak_points: list, mastery_level: str) -> str:
    """生成学习建议"""
    suggestions = []
    
    if mastery_level == 'mastered':
        suggestions.append("✅ 你已经掌握了这个知识点，建议按计划复习保持记忆。")
    elif mastery_level == 'learning':
        suggestions.append("📚 继续加油！建议多做一些变式题来巩固理解。")
        if weak_points:
            suggestions.append(f"重点关注：{', '.join(weak_points[:3])}")
    else:
        suggestions.append("💪 建议重新学习该知识点的基础内容。")
        suggestions.append("可以使用费曼讲解方法来检验理解程度。")
        if weak_points:
            suggestions.append(f"薄弱环节：{', '.join(weak_points[:3])}")
    
    return '\n'.join(suggestions)


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除非法字符"""
    import re
    # 移除非法字符
    filename = re.sub(r'[\\/*?:"()<>|]', "", filename)
    # 移除前后空格
    filename = filename.strip()
    # 限制长度
    if len(filename) > 100:
        filename = filename[:100]
    return filename


def format_duration(seconds: int) -> str:
    """格式化持续时间"""
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        return f"{seconds // 60}分钟"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}小时{minutes}分钟"
