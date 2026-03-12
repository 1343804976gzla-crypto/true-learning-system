"""
答案规范化 — 单一权威实现 (Single Source of Truth)

所有需要规范化答案字母的地方，必须调用此模块的函数。
禁止在其他文件中使用 re.search/re.findall 自行提取答案字母。
"""
import re


def normalize_answer(raw: str) -> str:
    """
    从原始答案字符串中提取纯 A-E 字母，排序去重。

    兼容格式：
      - "B"          → "B"
      - "B. 某选项"   → "B"
      - "AC"         → "AC"   (多选题)
      - "A、C"       → "AC"   (多选题中文顿号)
      - "A,C"        → "AC"   (多选题逗号)
      - ""           → ""
    """
    return "".join(sorted(set(re.findall(r"[A-E]", (raw or "").strip().upper()))))


def answers_match(user_answer: str, correct_answer: str) -> bool:
    """
    判断用户答案与正确答案是否匹配。
    两侧都先规范化后再比较，防止格式差异导致误判。
    """
    return normalize_answer(user_answer) == normalize_answer(correct_answer)
