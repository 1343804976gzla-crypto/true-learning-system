"""
SM-2 间隔重复算法 — 单一权威实现 (Single Source of Truth)

所有需要 SM-2 更新的地方，必须调用此模块的函数。
禁止在其他文件中自行实现 SM-2 逻辑。
"""

from datetime import date, datetime, timedelta


def sm2_update(wa, quality: int):
    """
    SM-2 间隔重复算法更新。
    quality: 0-5 评分
      5 = 答对+确定
      4 = 答对+模糊
      3 = 答对+不确定
      1 = 答错+模糊
      0 = 答错+确定（盲区）
    """
    ef = wa.sm2_ef or 2.5
    reps = wa.sm2_repetitions or 0
    interval = wa.sm2_interval or 0

    if quality >= 3:
        # 答对：延长间隔
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 3
        elif reps == 3:
            interval = 7
        else:
            interval = min(int(interval * ef), 60)  # 最长60天
        # 更新 EF
        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    else:
        # 答错：重置
        reps = 0
        interval = 1
        # EF 下降
        ef = ef - 0.2

    # EF 下限 1.3
    ef = max(1.3, ef)

    wa.sm2_ef = round(ef, 2)
    wa.sm2_repetitions = reps
    wa.sm2_interval = interval
    wa.next_review_date = date.today() + timedelta(days=interval)

    # 连续正确 3 次 → mastered
    if reps >= 3:
        wa.mastery_status = "archived"
        wa.archived_at = datetime.now()


def quality_from_result(is_correct: bool, confidence: str) -> int:
    """根据答题结果和自信度计算 SM-2 quality 评分"""
    if is_correct:
        if confidence == "sure":
            return 5
        elif confidence == "unsure":
            return 4
        else:
            return 3
    else:
        if confidence == "sure":
            return 0  # 盲区
        elif confidence == "unsure":
            return 1
        else:
            return 1
