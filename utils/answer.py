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
    text = (raw or "").strip().upper()
    if not text:
        return ""

    separator_pattern = r"[\s、，,;/；和与及+\-]+"

    def _normalize_group(group: str) -> str:
        letters = re.findall(r"[A-E]", group)
        return "".join(sorted(set(letters)))

    exact_compact = re.sub(separator_pattern, "", text)
    if exact_compact and re.fullmatch(r"[A-E]+", exact_compact):
        return _normalize_group(exact_compact)

    leading_match = re.match(
        rf"^\s*([A-E](?:{separator_pattern}[A-E])*)(?:[\s\.\)、,:：]|$)",
        text,
    )
    if leading_match:
        return _normalize_group(leading_match.group(1))

    marker_match = re.search(
        rf"(?:答案|ANSWER|SELECT|CHOOSE|选|选择)(?:是|为|:|：|\s)*([A-E](?:{separator_pattern}[A-E])*)",
        text,
    )
    if marker_match:
        return _normalize_group(marker_match.group(1))

    single_match = re.search(r"(?<![A-Z])[A-E](?![A-Z])", text)
    if single_match:
        return single_match.group(0)

    return ""


def answers_match(user_answer: str, correct_answer: str) -> bool:
    """
    判断用户答案与正确答案是否匹配。
    两侧都先规范化后再比较，防止格式差异导致误判。
    """
    return normalize_answer(user_answer) == normalize_answer(correct_answer)
