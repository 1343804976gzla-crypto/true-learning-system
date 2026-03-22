from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import math
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from learning_tracking_models import (
    ChapterReviewChapter,
    ChapterReviewTask,
    ChapterReviewTaskQuestion,
    ChapterReviewUnit,
    INVALID_CHAPTER_IDS,
)
from models import Chapter, DailyUpload
from services.ai_client import get_ai_client


logger = logging.getLogger(__name__)

_review_generation_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "review_generation_context",
    default=None,
)

REVIEW_INTERVAL_DAYS = [1, 3, 7, 14]
DEFAULT_REVIEW_TIME_BUDGET_MINUTES = 40
QUESTIONS_PER_REVIEW_UNIT = 10
UNIT_TARGET_CHARS = 900
UNIT_MAX_CHARS = 1350
GENERATION_SOURCE_TEXT_MAX_CHARS = 12000
GENERATION_BLUEPRINT_MAX_ITEMS = 16
OPEN_TASK_STATUSES = {"pending", "in_progress", "awaiting_choice"}
_TASK_QUESTION_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}
_TASK_QUESTION_LOCKS_GUARD = threading.Lock()
_SQLITE_LOCK_RETRY_ATTEMPTS = 3
GENERATION_SCOPE_UNIT_BUDGET_CHARS = 2400
GENERATION_SCOPE_BLUEPRINT_BLOCK_LIMIT = 12
GENERATION_POLISH_TRIGGER_NOISE_SCORE = 6
GENERATION_POLISH_SOURCE_MAX_CHARS = 12000
MEDICAL_TERM_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{2,10}(?:反馈|调节|机制|激素|受体|通路|效应|系统|细胞|因子|蛋白|激酶|抑制|激活|分泌|代谢|转运|信号|功能|作用|特点|意义|原因|结果|过程|阶段|分类|区别|联系|反应|吸收|消化|循环|通气|排卵|凝固)"
)
SPOKEN_PREFIXES = (
    "那么主要来看",
    "所以你看",
    "这就跟刚才学习",
    "这就跟刚才",
    "好最后再看",
    "好，最后再看",
    "好，再来看",
    "我们来看一下",
    "我们来看",
    "再来看",
    "接下来",
    "最后",
    "另外",
    "然后",
    "所以",
    "那么",
    "对吧",
    "你看",
    "那你",
    "其实",
    "就是",
    "这个",
    "那个",
)
GENERIC_FRAGMENT_WORDS = {
    "内容",
    "东西",
    "部分",
    "这里",
    "这个",
    "那个",
    "我们",
    "你看",
    "对吧",
    "一下",
    "一样",
    "一种",
    "一个",
}
GENERIC_CHAPTER_TITLES = {"绪论", "总论", "导论", "概述", "基础概念"}
SPOKEN_NOISE_MARKERS = (
    "对吧",
    "你看",
    "是不是",
    "按理来说",
    "一般来讲",
    "主要来看",
    "概念看一下",
    "就可以了",
    "稍微了解",
    "这个选项",
    "我们的这个",
    "这个时候",
    "好，但是",
    "好，最后",
)
WEAK_CONCEPT_PREFIXES = (
    "这个",
    "那个",
    "我们",
    "我们的",
    "你看",
    "对吧",
    "那么",
    "所以",
    "然后",
    "另外",
    "最后",
    "第二个",
    "第一个",
    "叫",
    "让",
    "往往",
    "如果",
    "并不是",
    "主要",
    "典型例子",
    "时候",
)
EXPLANATION_GUIDE_MARKERS = (
    "本题",
    "题眼",
    "核心",
    "关键",
    "作答",
    "答题",
    "易错",
    "失分",
    "误区",
    "陷阱",
    "注意",
    "先写",
    "再写",
    "不能只",
)
EXPLANATION_CAUSAL_MARKERS = (
    "因为",
    "因此",
    "所以",
    "从而",
    "由此",
    "意味着",
    "本质上",
    "关键在于",
    "体现为",
    "而不是",
    "区别在于",
)
GENERIC_EXPLANATION_SNIPPETS = (
    "请结合原文关键事实作答",
    "作答时尽量覆盖原文中的核心事实",
    "答案应围绕原文中的关键概念组织",
    "答案应覆盖原文中的三部分",
    "答案需要分别说明",
    "答案应围绕原文中的核心信息组织",
    "覆盖原文要点",
)
TOPIC_HINT_PATTERNS = (
    re.compile(r"(?:我们今天|今天|本节|这一节|这节|本章|接下来|继续|现在|一起来|我们再)\s*(?:来学习|学习|来看|看到|讲|讲解|复习|分析)\s*([^\n，。；;：:]{2,32})"),
    re.compile(r"(?:主题|章节|本章|本节)\s*[：:]\s*([^\n，。；;]{2,32})"),
)


@dataclass
class ReviewUnitDraft:
    unit_index: int
    unit_title: str
    raw_text: str
    cleaned_text: str
    excerpt: str
    char_count: int
    estimated_minutes: int


def _get_task_question_lock(actor_key: str, task_id: int) -> asyncio.Lock:
    key = (actor_key, int(task_id))
    with _TASK_QUESTION_LOCKS_GUARD:
        lock = _TASK_QUESTION_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _TASK_QUESTION_LOCKS[key] = lock
        return lock


def _is_retryable_sqlite_lock_error(exc: Exception) -> bool:
    return "database is locked" in str(exc).lower()


def _normalize_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _sequence_ratio(left: str, right: str) -> float:
    left_key = _normalize_match_key(left)
    right_key = _normalize_match_key(right)
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _trim_text(value: str, *, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip("，、；;。！？!? ") + "…"


def _collapse_repeated_terms(text: str) -> str:
    return re.sub(r"((?:[\u4e00-\u9fff]{1,4}|[A-Za-z]{2,10}))\1{2,}", r"\1", text)


def _strip_spoken_prefixes(value: str) -> str:
    text = str(value or "").strip()
    while text:
        original = text
        text = text.lstrip("，,。；;：:、 ")
        for prefix in SPOKEN_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip("，,。；;：:、 ")
        if text == original:
            break
    return text


def _clean_fragment(value: str) -> str:
    text = _normalize_text(str(value or ""))
    text = _strip_spoken_prefixes(text)
    text = text.strip("，,。；;：:、（）()[]【】 ")
    return text


def _clean_concept_candidate(value: str) -> str:
    text = _clean_fragment(value)
    text = re.sub(r"^(?:的|其|它|他|她|该|此|这|那)+", "", text).strip()
    text = re.sub(r"^(?:它是个|它是|这是个|这是|属于|是一种|是一类|是个|是种|是类)", "", text).strip()
    text = re.sub(r"^(?:如果|但是|不过|所以|因此|然后|那么|一下|这个时候|这个|那个)\s*", "", text).strip()
    text = re.sub(r"^(?:这个|那个|该|本|上述|这种|这一|此类|这类|这些|那些)", "", text).strip()
    text = re.sub(r"^(?:有关|关于|对于|一种|一个|一些)", "", text).strip()
    text = re.sub(r"^(?:和|与|及|并|而|并且|或者|其中|以及)\s*", "", text).strip()
    text = re.sub(r"^(.{2,18}?)(?:就是|会|可|能|使|可使|会使|激活|抑制|促进|增强|减弱).*$", r"\1", text).strip()
    text = re.sub(r"^([\u4e00-\u9fffA-Za-z]{2,16}?)(?:会使|可使|能使|使|通过|借助|依赖|导致|引起|维持|促进|抑制|增强|减弱).*$", r"\1", text).strip()
    text = re.sub(r"(?:的核心机制|的核心内涵|的答题抓手|的关键环节|核心要点|相关内容)$", "", text).strip()
    text = re.sub(r"(?:的内容|这个部分|这一部分|这一节|这个章节|这个东西)$", "", text).strip()
    text = re.sub(r"(?:与相关概念|与相近概念|和相关概念|和相近概念)$", "", text).strip()
    text = re.sub(r"^(?:第[一二三四五六七八九十百]+个|第\d+个)", "", text).strip()
    text = text.strip("，,。；;：:、 ")
    if len(text) < 2:
        return ""
    if text in GENERIC_FRAGMENT_WORDS:
        return ""
    if _is_weak_concept(text):
        return ""
    if len(text) > 22:
        text = text[:22].rstrip("，,。；;：:、 ")
    return text


def _is_weak_concept(value: str) -> bool:
    text = str(value or "").strip("，,。；;：:、 ")
    if not text:
        return True
    if text.startswith("请") and len(text) <= 4:
        return True
    if re.match(r"^[的其它他她该此这那]", text):
        return True
    if re.match(r"^(?:如果|但是|不过|所以|因此|然后|那么|一下|这个时候|这个|那个|我们的|你的|我的)", text):
        return True
    if any(marker in text for marker in ("就是", "可以了", "答题抓手", "关键环节", "核心内涵")):
        return True
    if any(marker in text for marker in ("才是", "绝对不", "一直加强", "相关概念", "辩证作用", "识别")):
        return True
    if text in GENERIC_FRAGMENT_WORDS or text in GENERIC_CHAPTER_TITLES:
        return True
    if any(marker in text for marker in ("所以", "那么", "是不是", "刚才", "这句话", "下句话")):
        return True
    if any(marker in text for marker in ("让我们", "通过我们", "帮助我们", "要是没有", "并不是所有", "她通过一个", "他通过一个")):
        return True
    if len(text) > 6 and any(marker in text for marker in ("一个", "这个", "那个", "这种", "这类")):
        return True
    if any(marker in text for marker in ("对吧", "你看", "概念看一下", "主要来看", "就可以了")):
        return True
    if any(text.startswith(prefix) for prefix in WEAK_CONCEPT_PREFIXES):
        return True
    if re.search(r"[，。；;：:]", text):
        return True
    if re.search(r"(?:什么|哪些|时候|这样|这种)$", text):
        return True
    return False


def _chapter_focus_from_unit_title(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*[·•\-－—]\s*单元\s*\d+\s*$", "", text)
    text = re.sub(r"\s*单元\s*\d+\s*$", "", text)
    return _clean_concept_candidate(text)


def _spoken_noise_score(text: str) -> int:
    content = str(text or "")
    score = sum(1 for marker in SPOKEN_NOISE_MARKERS if marker in content)
    score += len(re.findall(r"(?:啊|呀|呢|嘛)(?=[，。；！？!?]|$)", content))
    if re.search(r"(?:越来越强){2,}", content):
        score += 2
    return score


def _polish_source_sentence(value: str) -> str:
    text = _clean_fragment(value)
    if not text:
        return ""
    text = re.sub(
        r"^(?:那么|所以|然后|另外|最后|接下来|再来看|我们再看|我们来看|下面来看|你看|对吧|"
        r"好(?:，|。)?|按理来说啊?|一般来讲|主要来看|概念看一下就可以了|其实|就是说|就说)\s*",
        "",
        text,
    )
    text = re.sub(r"(?:对吧|你看|是不是)(?=[，。；！？!?]|$)", "", text)
    text = re.sub(r"(?:啊|呀|嘛|呢)(?=[，。；！？!?]|$)", "", text)
    text = re.sub(r"(?:我们的这个|这个|那个)(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"[，,]\s*(?:那么|所以|然后|另外|最后|好)\s*", "，", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。]{2,}", "。", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip("，,。；; ")


def _split_sentences(text: str) -> list[str]:
    return [
        _polish_source_sentence(item)
        for item in re.split(r"(?<=[。！？!?；;])|\n+", str(text or ""))
        if _polish_source_sentence(item)
    ]


def _extract_medical_terms(text: str, *, limit: int = 12) -> list[str]:
    terms: list[str] = []
    for raw in MEDICAL_TERM_PATTERN.findall(str(text or "")):
        cleaned = _clean_concept_candidate(raw)
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
        if len(terms) >= limit:
            break
    if len(terms) < limit:
        for fragment in re.split(r"[，,、；;。！？!?\n]", str(text or "")):
            cleaned = _clean_concept_candidate(fragment)
            if not cleaned or cleaned in terms:
                continue
            if 3 <= len(cleaned) <= 16:
                terms.append(cleaned)
            if len(terms) >= limit:
                break
    return terms[:limit]


def _normalize_topic_hint(value: str) -> str:
    text = _clean_fragment(value)
    text = re.sub(
        r"^(?:生理学|内科学|病理学|外科学|生物化学|诊断学|药理学|病理生理学|医学微生物学|医学免疫学)",
        "",
        text,
    ).strip()
    text = re.sub(r"^第[一二三四五六七八九十百千0-9]+章", "", text).strip()
    text = re.sub(r"(?:这个章节|这一章|这部分|的内容)$", "", text).strip()
    return text.strip("，,。；;：:、 ")


def _extract_topic_hints(raw_text: str) -> list[str]:
    head = _normalize_text(raw_text)[:1400]
    hints: list[str] = []
    for pattern in TOPIC_HINT_PATTERNS:
        for match in pattern.finditer(head):
            hint = _normalize_topic_hint(match.group(1))
            if hint and hint not in hints:
                hints.append(hint)
    return hints


def _chapter_candidate_text(chapter: Chapter) -> str:
    parts = [str(getattr(chapter, "chapter_title", "") or ""), str(getattr(chapter, "content_summary", "") or "")]
    for item in list(getattr(chapter, "concepts", None) or [])[:10]:
        if isinstance(item, dict):
            parts.append(str(item.get("name") or ""))
        else:
            parts.append(str(item or ""))
    return " ".join(part for part in parts if part).strip()


def _keyword_overlap_score(keywords: list[str], candidate_text: str) -> int:
    candidate_key = _normalize_match_key(candidate_text)
    if not candidate_key:
        return 0
    score = 0
    for keyword in keywords:
        keyword_key = _normalize_match_key(keyword)
        if keyword_key and keyword_key in candidate_key:
            score += 1
    return score


def _candidate_direct_hit_score(chapter: Chapter, raw_text: str) -> int:
    raw_key = _normalize_match_key(raw_text)
    if not raw_key:
        return 0
    hits = 0
    terms = [str(getattr(chapter, "chapter_title", "") or "")]
    for item in list(getattr(chapter, "concepts", None) or [])[:12]:
        if isinstance(item, dict):
            terms.append(str(item.get("name") or ""))
        else:
            terms.append(str(item or ""))
    for term in terms:
        term_key = _normalize_match_key(term)
        if len(term_key) >= 2 and term_key in raw_key:
            hits += 1
    return hits


def _pick_better_review_chapter(
    db: Session,
    *,
    raw_text: str,
    book: str,
    chapter_id: str,
    chapter_title: str,
) -> Optional[Chapter]:
    topic_hints = _extract_topic_hints(raw_text)
    content_keywords = _extract_medical_terms(raw_text, limit=18)
    if not topic_hints and not content_keywords:
        return None

    current_title_score = max((_sequence_ratio(chapter_title, hint) for hint in topic_hints), default=0.0)
    current_keyword_score = _keyword_overlap_score(content_keywords, chapter_title)
    current_score = current_title_score + min(current_keyword_score, 4) * 0.08

    best_row: tuple[float, float, int, Chapter] | None = None
    for scope_book in [book, ""]:
        query = db.query(Chapter)
        if scope_book:
            query = query.filter(Chapter.book == scope_book)
        candidates = query.all()
        for candidate in candidates:
            title = str(candidate.chapter_title or "").strip()
            if not title:
                continue
            title_score = max((_sequence_ratio(title, hint) for hint in topic_hints), default=0.0)
            keyword_score = _keyword_overlap_score(content_keywords, _chapter_candidate_text(candidate))
            direct_hit_score = _candidate_direct_hit_score(candidate, raw_text)
            score = title_score + min(keyword_score, 5) * 0.08 + min(direct_hit_score, 4) * 0.1
            if scope_book and str(candidate.book or "").strip() == book:
                score += 0.04
            if score <= 0:
                continue
            row = (score, title_score, keyword_score + direct_hit_score, candidate)
            if best_row is None or row[:3] > best_row[:3]:
                best_row = row
        if best_row is not None and best_row[0] >= 0.62:
            break

    if best_row is None:
        return None

    best_score, best_title_score, best_keyword_score, best_candidate = best_row
    if str(best_candidate.id or "").strip() == chapter_id:
        return None
    if best_score < 0.62 and not ((best_title_score >= 0.48 and best_keyword_score >= 2) or (best_keyword_score >= 2 and best_score >= 0.34)):
        return None
    if current_score and best_score < current_score + 0.16:
        return None
    return best_candidate


def _normalize_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_review_content(raw_text: str) -> str:
    cleaned = _normalize_text(raw_text)
    cleaned = re.sub(r"(?m)^---\s*上传补充\s+\d{4}-\d{2}-\d{2}\s*---\s*$", "", cleaned)
    cleaned = re.sub(r"^(?:hello|哈喽)?各位同学[^\n。！？!?]{0,80}(?:直播间|晚上好)[，, ]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^我是天天师[兄生][^\n。！？!?]{0,40}[，, ]*", "", cleaned)
    cleaned = _collapse_repeated_terms(cleaned)
    cleaned = re.sub(
        r"(?:(?<=^)|(?<=[。！？!?；;\n]))\s*(?:那么|然后|另外|最后|接下来|再来看|我们再看|我们来看|"
        r"下面来看|你看|对吧|好(?:，|。)?|一般来讲|按理来说啊?)\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?<=[。！？!?；;])\s*(?=(?:首先|其次|接下来|然后|另外|最后|再来看|我们再看|我们来看|下面来看))",
        "\n\n",
        cleaned,
    )
    lines = [line.strip() for line in cleaned.split("\n")]
    normalized_lines: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                normalized_lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        normalized_lines.append(line)
    return "\n".join(normalized_lines).strip()


def _split_large_segment(segment: str, max_chars: int) -> list[str]:
    cleaned = segment.strip()
    if len(cleaned) <= max_chars:
        return [cleaned] if cleaned else []

    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?；;])", cleaned)
        if item and item.strip()
    ]
    if len(sentences) <= 1:
        return [cleaned[i:i + max_chars] for i in range(0, len(cleaned), max_chars) if cleaned[i:i + max_chars].strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and current_chars + len(sentence) > max_chars:
            chunks.append("".join(current).strip())
            current = [sentence]
            current_chars = len(sentence)
        else:
            current.append(sentence)
            current_chars += len(sentence)
    if current:
        chunks.append("".join(current).strip())
    return [item for item in chunks if item]


def _extract_segments(cleaned_text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", cleaned_text) if item and item.strip()]
    if not paragraphs:
        return [cleaned_text.strip()] if cleaned_text.strip() else []

    segments: list[str] = []
    for paragraph in paragraphs:
        paragraph = _normalize_text(paragraph)
        paragraph = re.sub(
            r"(?<=[。！？!?；;])\s*(?=(?:第一|第二|第三|首先|其次|接下来|然后|另外|最后|再来看|我们再看|我们来看))",
            "\n\n",
            paragraph,
        )
        paragraph_segments = [item.strip() for item in re.split(r"\n{2,}", paragraph) if item and item.strip()]
        for item in paragraph_segments or [paragraph]:
            segments.extend(_split_large_segment(item, max_chars=UNIT_MAX_CHARS))
    return [item for item in segments if item]


def estimate_unit_minutes(text: str, *, question_count: int = QUESTIONS_PER_REVIEW_UNIT) -> int:
    char_count = max(len(text.strip()), 1)
    reading_minutes = max(4, math.ceil(char_count / 320))
    answer_minutes = max(8, math.ceil(question_count * 1.2))
    return min(28, reading_minutes + answer_minutes)


def build_review_units(cleaned_text: str, *, chapter_title: str) -> list[ReviewUnitDraft]:
    segments = _extract_segments(cleaned_text)
    if not segments:
        return []

    units: list[ReviewUnitDraft] = []
    current_parts: list[str] = []
    current_chars = 0

    def flush_current() -> None:
        nonlocal current_parts, current_chars
        if not current_parts:
            return
        raw_text = "\n\n".join(current_parts).strip()
        unit_number = len(units) + 1
        units.append(
            ReviewUnitDraft(
                unit_index=unit_number,
                unit_title=f"{chapter_title} · 单元 {unit_number}",
                raw_text=raw_text,
                cleaned_text=raw_text,
                excerpt=raw_text[:180].strip(),
                char_count=len(raw_text),
                estimated_minutes=estimate_unit_minutes(raw_text),
            )
        )
        current_parts = []
        current_chars = 0

    for segment in segments:
        segment_chars = len(segment)
        if current_parts and current_chars + segment_chars > UNIT_MAX_CHARS:
            flush_current()
        current_parts.append(segment)
        current_chars += segment_chars
        if current_chars >= UNIT_TARGET_CHARS:
            flush_current()

    flush_current()
    return units


def _task_unit_source_text(task: ChapterReviewTask) -> str:
    return clean_review_content(task.unit.cleaned_text or task.unit.raw_text or task.unit.excerpt or task.unit.unit_title)


def _task_chapter_source_text(task: ChapterReviewTask) -> str:
    return clean_review_content(
        task.review_chapter.cleaned_content
        or task.review_chapter.merged_raw_content
        or task.unit.cleaned_text
        or task.unit.raw_text
        or task.unit.excerpt
        or task.review_chapter.chapter_title
    )


def _build_generation_scope_text(
    *,
    unit_source_text: str,
    chapter_source_text: str,
    blueprint: list[dict[str, Any]],
    max_chars: int = GENERATION_SOURCE_TEXT_MAX_CHARS,
) -> str:
    unit_clean = clean_review_content(unit_source_text)
    chapter_clean = clean_review_content(chapter_source_text)
    if not chapter_clean:
        return unit_clean
    if len(chapter_clean) <= max_chars:
        return chapter_clean

    selected_blocks: list[str] = []
    seen_keys: set[str] = set()
    total_chars = 0

    def add_block(text: str, *, required: bool = False) -> None:
        nonlocal total_chars
        cleaned = clean_review_content(text)
        if not cleaned:
            return
        key = _normalize_match_key(cleaned[:200]) or _normalize_match_key(cleaned)
        if key and key in seen_keys:
            return

        join_cost = 2 if selected_blocks else 0
        remaining = max_chars - total_chars - join_cost
        if remaining <= 0:
            return
        if len(cleaned) > remaining:
            if not required and remaining < 140:
                return
            trimmed = cleaned[:remaining].rstrip()
            sentence_cut = max(trimmed.rfind("。"), trimmed.rfind("；"), trimmed.rfind("！"), trimmed.rfind("？"))
            if sentence_cut >= 48:
                cleaned = trimmed[: sentence_cut + 1]
            else:
                cleaned = trimmed.rstrip("，,；; ") + "…"
        if not cleaned:
            return
        if key:
            seen_keys.add(key)
        selected_blocks.append(cleaned)
        total_chars += len(cleaned) + join_cost

    unit_segments = _extract_segments(unit_clean) or ([unit_clean] if unit_clean else [])
    for segment in unit_segments:
        add_block(segment, required=not selected_blocks)
        if total_chars >= GENERATION_SCOPE_UNIT_BUDGET_CHARS:
            break

    for item in blueprint[:GENERATION_SCOPE_BLUEPRINT_BLOCK_LIMIT]:
        add_block(str(item.get("supporting_text") or item.get("source_excerpt") or ""))

    chapter_segments = _extract_segments(chapter_clean)
    if chapter_segments:
        add_block(chapter_segments[0])
        if len(chapter_segments) > 2:
            add_block(chapter_segments[len(chapter_segments) // 2])
        if len(chapter_segments) > 1:
            add_block(chapter_segments[-1])
    for segment in chapter_segments:
        add_block(segment)
        if total_chars >= max_chars or len(selected_blocks) >= 16:
            break

    scoped = "\n\n".join(block for block in selected_blocks if block).strip()
    if scoped:
        return scoped
    return _trim_text(chapter_clean, max_length=max_chars)


def _append_merged_content(existing: str, addition: str, *, upload_date: date) -> str:
    incoming = str(addition or "").strip()
    if not incoming:
        return str(existing or "").strip()

    existing_text = str(existing or "").strip()
    if not existing_text:
        return incoming
    if incoming in existing_text:
        return existing_text

    divider = f"\n\n--- 上传补充 {upload_date.isoformat()} ---\n"
    return f"{existing_text}{divider}{incoming}".strip()


def _chapter_review_query(db: Session, *, actor_key: str, chapter_id: str):
    return (
        db.query(ChapterReviewChapter)
        .options(joinedload(ChapterReviewChapter.units), joinedload(ChapterReviewChapter.tasks))
        .filter(
            ChapterReviewChapter.actor_key == actor_key,
            ChapterReviewChapter.chapter_id == chapter_id,
        )
    )


def _recompute_chapter_due(review_chapter: ChapterReviewChapter) -> None:
    active_units = [unit for unit in review_chapter.units if unit.is_active]
    due_dates = [unit.next_due_date for unit in active_units if unit.next_due_date]
    review_chapter.total_units = len(active_units)
    review_chapter.total_estimated_minutes = int(sum(unit.estimated_minutes or 0 for unit in active_units))
    review_chapter.next_due_date = min(due_dates) if due_dates else None
    if not active_units:
        review_chapter.review_status = "completed"
        return
    if any(unit.review_status == "weak" for unit in active_units):
        review_chapter.review_status = "weak"
    elif any(unit.next_due_date and unit.next_due_date <= date.today() for unit in active_units):
        review_chapter.review_status = "due"
    else:
        review_chapter.review_status = "pending"


def sync_review_chapter_from_upload(
    db: Session,
    *,
    actor_key: str,
    upload_record: DailyUpload,
    chapter: Optional[Chapter],
    extracted: Dict[str, Any],
) -> Optional[ChapterReviewChapter]:
    chapter_id = str((extracted or {}).get("chapter_id") or "").strip()
    if not chapter_id or chapter_id in INVALID_CHAPTER_IDS or chapter_id.endswith("_ch0"):
        return None

    chapter_title = str((extracted or {}).get("chapter_title") or getattr(chapter, "chapter_title", "") or "未识别章节").strip()
    book = str((extracted or {}).get("book") or getattr(chapter, "book", "") or "未识别").strip()
    summary = str((extracted or {}).get("summary") or getattr(chapter, "content_summary", "") or "").strip()
    upload_date = upload_record.date or date.today()
    override_chapter = _pick_better_review_chapter(
        db,
        raw_text=str(upload_record.raw_content or ""),
        book=book,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
    )
    if override_chapter is not None:
        chapter = override_chapter
        chapter_id = str(override_chapter.id or "").strip() or chapter_id
        chapter_title = str(override_chapter.chapter_title or "").strip() or chapter_title
        book = str(override_chapter.book or "").strip() or book

    review_chapter = _chapter_review_query(db, actor_key=actor_key, chapter_id=chapter_id).first()
    is_update = review_chapter is not None

    if review_chapter is None:
        review_chapter = ChapterReviewChapter(
            actor_key=actor_key,
            chapter_id=chapter_id,
            book=book,
            chapter_number=str(getattr(chapter, "chapter_number", "") or (extracted or {}).get("chapter_number") or "").strip(),
            chapter_title=chapter_title,
            ai_summary=summary or None,
            merged_raw_content=str(upload_record.raw_content or "").strip(),
            cleaned_content="",
            content_version=1,
            first_uploaded_date=upload_date,
            last_uploaded_date=upload_date,
            next_due_date=upload_date + timedelta(days=REVIEW_INTERVAL_DAYS[0]),
            review_status="pending",
        )
        db.add(review_chapter)
        db.flush()
    else:
        review_chapter.book = book or review_chapter.book
        review_chapter.chapter_number = str(getattr(chapter, "chapter_number", "") or review_chapter.chapter_number or "").strip()
        review_chapter.chapter_title = chapter_title or review_chapter.chapter_title
        review_chapter.ai_summary = summary or review_chapter.ai_summary
        review_chapter.merged_raw_content = _append_merged_content(
            review_chapter.merged_raw_content,
            upload_record.raw_content,
            upload_date=upload_date,
        )
        review_chapter.last_uploaded_date = upload_date
        review_chapter.content_version = int(review_chapter.content_version or 0) + 1
        review_chapter.next_due_date = upload_date + timedelta(days=REVIEW_INTERVAL_DAYS[0])
        review_chapter.review_status = "pending"

    if not is_update:
        review_chapter.merged_raw_content = str(upload_record.raw_content or "").strip()

    review_chapter.cleaned_content = clean_review_content(review_chapter.merged_raw_content)
    review_chapter.ai_summary = _resolve_review_summary(
        review_chapter.ai_summary or summary or "",
        source_text=review_chapter.cleaned_content,
        chapter_title=review_chapter.chapter_title,
    ) or review_chapter.ai_summary
    unit_drafts = build_review_units(review_chapter.cleaned_content, chapter_title=review_chapter.chapter_title)
    if not unit_drafts:
        cleaned = review_chapter.cleaned_content or str(upload_record.raw_content or "").strip()
        unit_drafts = [
            ReviewUnitDraft(
                unit_index=1,
                unit_title=f"{review_chapter.chapter_title} · 单元 1",
                raw_text=cleaned,
                cleaned_text=cleaned,
                excerpt=cleaned[:180].strip(),
                char_count=len(cleaned),
                estimated_minutes=estimate_unit_minutes(cleaned),
            )
        ]

    if is_update:
        for unit in review_chapter.units:
            if unit.is_active:
                unit.is_active = False
                unit.review_status = "archived"
        for task in review_chapter.tasks:
            if task.status in OPEN_TASK_STATUSES:
                task.status = "cancelled"
                task.updated_at = datetime.now()

    version = int(review_chapter.content_version or 1)
    first_due_date = upload_date + timedelta(days=REVIEW_INTERVAL_DAYS[0])
    for draft in unit_drafts:
        review_chapter.units.append(
            ChapterReviewUnit(
                content_version=version,
                unit_index=draft.unit_index,
                unit_title=draft.unit_title,
                raw_text=draft.raw_text,
                cleaned_text=draft.cleaned_text,
                excerpt=draft.excerpt,
                char_count=draft.char_count,
                estimated_minutes=draft.estimated_minutes,
                next_round=1,
                completed_rounds=0,
                next_due_date=first_due_date,
                review_status="pending",
                carry_over_count=0,
                is_active=True,
            )
        )

    _recompute_chapter_due(review_chapter)
    return review_chapter


def _serialize_task_summary(task: ChapterReviewTask, *, today: date) -> Dict[str, Any]:
    review_chapter = task.review_chapter
    unit = task.unit
    unit_source_text = clean_review_content(unit.cleaned_text or unit.raw_text or unit.excerpt or unit.unit_title)
    summary = _resolve_review_summary(
        review_chapter.ai_summary or "",
        source_text=unit_source_text,
        chapter_title=review_chapter.chapter_title,
    )
    answered_count = sum(1 for question in task.questions if str(question.user_answer or "").strip())
    total_questions = int(task.question_count or len(task.questions) or QUESTIONS_PER_REVIEW_UNIT)
    remaining_questions = max(total_questions - answered_count, 0)
    carry_over_days = max((today - task.scheduled_for).days, 0)
    if carry_over_days > 0 and task.status != "completed":
        due_reason = f"昨日未完成，顺延 {carry_over_days} 天"
    else:
        due_reason = task.due_reason

    mastery_status = unit.review_status or "pending"
    if task.status == "in_progress":
        mastery_status = "in_progress"
    elif task.status == "awaiting_choice":
        mastery_status = "awaiting_choice"

    return {
        "task_id": int(task.id),
        "chapter_id": review_chapter.chapter_id,
        "book": review_chapter.book,
        "chapter_title": review_chapter.chapter_title,
        "unit_id": int(unit.id),
        "unit_title": unit.unit_title,
        "unit_index": int(unit.unit_index),
        "excerpt": unit.excerpt or unit.cleaned_text[:180],
        "summary": summary,
        "estimated_minutes": int(task.estimated_minutes or unit.estimated_minutes or 0),
        "due_reason": due_reason,
        "mastery_status": mastery_status,
        "next_round": int(unit.next_round or 1),
        "answered_count": answered_count,
        "question_count": total_questions,
        "remaining_questions": remaining_questions,
        "resume_position": int(task.resume_position or 0),
        "scheduled_for": task.scheduled_for.isoformat(),
        "carry_over_days": carry_over_days,
        "status": task.status,
        "ai_recommended_status": task.ai_recommended_status,
        "user_selected_status": task.user_selected_status,
        "grading_score": task.grading_score,
    }


def serialize_task_detail(task: ChapterReviewTask) -> Dict[str, Any]:
    payload = _serialize_task_summary(task, today=date.today())
    payload["content_version"] = int(
        task.content_version
        or getattr(task.unit, "content_version", 0)
        or getattr(task.review_chapter, "content_version", 0)
        or 1
    )
    payload["source_content"] = _task_chapter_source_text(task)
    payload["questions"] = [
        {
            "id": int(question.id),
            "position": int(question.position),
            "prompt": question.prompt,
            "reference_answer": question.reference_answer,
            "key_points": list(question.key_points or []),
            "explanation": question.explanation or "",
            "source_excerpt": question.source_excerpt or "",
            "generation_source": question.generation_source or "",
            "user_answer": question.user_answer or "",
            "ai_score": question.ai_score,
            "ai_feedback": question.ai_feedback or "",
            "good_points": list(question.good_points or []),
            "missing_points": list(question.missing_points or []),
            "improvement_suggestion": question.improvement_suggestion or "",
        }
        for question in sorted(task.questions, key=lambda item: item.position)
    ]
    return payload


def _candidate_bucket(unit: ChapterReviewUnit, *, target_date: date) -> tuple[str, int]:
    if unit.next_due_date and unit.next_due_date <= target_date:
        overdue_days = (target_date - unit.next_due_date).days
        return "due", overdue_days

    if unit.last_reviewed_at:
        dormant_days = (target_date - unit.last_reviewed_at).days
        if dormant_days >= 21:
            return "stale", dormant_days

    return "new", 0


def _create_task_for_unit(
    db: Session,
    *,
    actor_key: str,
    unit: ChapterReviewUnit,
    target_date: date,
    bucket: str,
    extra_score: int,
) -> ChapterReviewTask:
    due_reason_map = {
        "due": f"第 {int(unit.next_round or 1)} 轮到期复习",
        "stale": "很久没复习，重新唤醒",
        "new": "最近新上传，首次进入复习",
    }
    task = ChapterReviewTask(
        actor_key=actor_key,
        review_chapter_id=int(unit.review_chapter_id),
        unit_id=int(unit.id),
        content_version=int(unit.content_version or 1),
        scheduled_for=target_date,
        due_reason=due_reason_map[bucket],
        priority_bucket=bucket,
        priority_score=float(extra_score),
        estimated_minutes=int(unit.estimated_minutes or 0),
        question_count=QUESTIONS_PER_REVIEW_UNIT,
        status="pending",
        source_label=due_reason_map[bucket],
    )
    db.add(task)
    return task


def _task_has_saved_progress(task: ChapterReviewTask) -> bool:
    if int(task.answered_count or 0) > 0:
        return True
    return any(str(question.user_answer or "").strip() for question in list(task.questions or []))


def _refresh_stale_open_tasks(
    db: Session,
    *,
    open_tasks: list[ChapterReviewTask],
    review_date: date,
) -> None:
    refreshed = False
    now = datetime.now()
    for task in open_tasks:
        if task.status != "pending":
            continue
        if not task.scheduled_for or task.scheduled_for >= review_date:
            continue
        if _task_has_saved_progress(task):
            continue
        last_updated = task.updated_at.date() if task.updated_at else None
        if last_updated is not None and last_updated >= review_date:
            continue
        for question in list(task.questions or []):
            db.delete(question)
        task.resume_position = 0
        task.answered_count = 0
        task.ai_recommended_status = None
        task.user_selected_status = None
        task.grading_score = None
        task.started_at = None
        task.graded_at = None
        task.completed_at = None
        task.updated_at = now
        refreshed = True
    if refreshed:
        db.flush()
        for task in open_tasks:
            db.refresh(task)


def ensure_daily_review_plan(
    db: Session,
    *,
    actor_key: str,
    target_date: Optional[date] = None,
    time_budget_minutes: int = DEFAULT_REVIEW_TIME_BUDGET_MINUTES,
) -> Dict[str, Any]:
    review_date = target_date or date.today()
    budget = max(15, int(time_budget_minutes or DEFAULT_REVIEW_TIME_BUDGET_MINUTES))

    open_tasks = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(
            ChapterReviewTask.actor_key == actor_key,
            ChapterReviewTask.status.in_(sorted(OPEN_TASK_STATUSES)),
        )
        .order_by(ChapterReviewTask.status.desc(), ChapterReviewTask.scheduled_for.asc(), ChapterReviewTask.id.asc())
        .all()
    )
    _refresh_stale_open_tasks(db, open_tasks=open_tasks, review_date=review_date)

    open_by_unit = {int(task.unit_id): task for task in open_tasks}
    selected_tasks: list[ChapterReviewTask] = list(open_tasks)
    used_minutes = int(sum(task.estimated_minutes or 0 for task in selected_tasks))

    active_units = (
        db.query(ChapterReviewUnit)
        .options(joinedload(ChapterReviewUnit.review_chapter))
        .join(ChapterReviewChapter, ChapterReviewChapter.id == ChapterReviewUnit.review_chapter_id)
        .filter(
            ChapterReviewChapter.actor_key == actor_key,
            ChapterReviewUnit.is_active.is_(True),
        )
        .order_by(ChapterReviewChapter.updated_at.desc(), ChapterReviewUnit.unit_index.asc())
        .all()
    )

    candidates: list[tuple[str, int, ChapterReviewUnit]] = []
    for unit in active_units:
        if int(unit.id) in open_by_unit:
            continue
        if int(unit.completed_rounds or 0) >= len(REVIEW_INTERVAL_DAYS):
            continue
        bucket, score = _candidate_bucket(unit, target_date=review_date)
        candidates.append((bucket, score, unit))

    bucket_priority = {"due": 0, "stale": 1, "new": 2}
    candidates.sort(
        key=lambda item: (
            bucket_priority.get(item[0], 9),
            -int(item[1] or 0),
            item[2].next_due_date or date.max,
            -(item[2].char_count or 0),
            int(item[2].id),
        )
    )

    for bucket, score, unit in candidates:
        estimated = int(unit.estimated_minutes or 0)
        if selected_tasks and used_minutes + estimated > budget:
            continue
        if not selected_tasks and estimated > budget:
            task = _create_task_for_unit(db, actor_key=actor_key, unit=unit, target_date=review_date, bucket=bucket, extra_score=score)
            selected_tasks.append(task)
            used_minutes += estimated
            break
        if used_minutes + estimated <= budget:
            task = _create_task_for_unit(db, actor_key=actor_key, unit=unit, target_date=review_date, bucket=bucket, extra_score=score)
            selected_tasks.append(task)
            used_minutes += estimated

    db.flush()

    refreshed_tasks = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(ChapterReviewTask.id.in_([int(task.id) for task in selected_tasks]) if selected_tasks else False)
        .order_by(ChapterReviewTask.scheduled_for.asc(), ChapterReviewTask.id.asc())
        .all()
        if selected_tasks
        else []
    )

    completed_tasks = (
        db.query(ChapterReviewTask)
        .filter(
            ChapterReviewTask.actor_key == actor_key,
            ChapterReviewTask.status == "completed",
            ChapterReviewTask.completed_at.isnot(None),
        )
        .all()
    )
    completed_today_count = sum(
        1
        for task in completed_tasks
        if task.completed_at and task.completed_at.date() == review_date
    )

    carry_over_count = sum(1 for task in refreshed_tasks if task.scheduled_for < review_date)
    remaining_minutes = max(budget - used_minutes, 0)
    tasks_payload = [_serialize_task_summary(task, today=review_date) for task in refreshed_tasks]

    return {
        "date": review_date.isoformat(),
        "time_budget_minutes": budget,
        "estimated_total_minutes": used_minutes,
        "remaining_minutes": remaining_minutes,
        "task_count": len(tasks_payload),
        "carry_over_count": carry_over_count,
        "completed_today_count": completed_today_count,
        "tasks": tasks_payload,
    }


def _pick_supporting_sentences(paragraph: str, concept: str, *, limit: int = 3) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    for sentence in _split_sentences(paragraph):
        if any(marker in sentence for marker in ("下一句话", "这句话", "要是没有", "比如说", "天天说", "这个时候", "你说它")):
            continue
        score = 0
        if concept and concept in sentence:
            score += 4
        if any(token in sentence for token in ("是", "指", "包括", "通过", "导致", "作用", "意义", "特点", "表现", "可见", "因此")):
            score += 2
        if any(token in sentence for token in ("机制", "调节", "反馈", "激活", "抑制", "分泌", "功能")):
            score += 2
        score += min(len(sentence), 80) // 20
        score -= min(_spoken_noise_score(sentence), 3) * 2
        if score <= 0:
            continue
        ranked.append((score, len(sentence), sentence))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected: list[str] = []
    seen: set[str] = set()
    for _, _, sentence in ranked:
        key = _normalize_match_key(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(sentence)
        if len(selected) >= limit:
            break
    return selected


def _build_reference_answer_from_segment(paragraph: str, concept: str) -> str:
    key_points = _build_key_points_from_segment(paragraph, concept)
    focus = concept or "该知识点"
    if key_points and all(not _is_weak_concept(point) for point in key_points[:3]):
        answer = f"{focus}的核心要点包括：{'；'.join(point.rstrip('。；; ') for point in key_points[:3])}。"
        return _trim_text(answer, max_length=180)

    sentences = _pick_supporting_sentences(paragraph, concept, limit=3)
    if not sentences:
        fallback = _polish_source_sentence(paragraph)
        sentences = [fallback] if fallback else []
    answer = "；".join(sentence.rstrip("。；; ") for sentence in sentences if sentence)
    return _trim_text(answer, max_length=180)


def _build_key_points_from_segment(paragraph: str, concept: str) -> list[str]:
    def _distill_clause(sentence: str) -> str:
        cleaned = _polish_source_sentence(sentence)
        if not cleaned:
            return ""
        clauses = [item.strip() for item in re.split(r"[，,；;：:]", cleaned) if item and item.strip()]
        distilled: list[str] = []
        for clause in clauses or [cleaned]:
            clause = re.sub(r"^(?:如果|但是|不过|所以|因此|然后|那么|一下|这个时候|这个|那个|我们的|你的|我的)\s*", "", clause).strip()
            clause = re.sub(r"^(?:它是个|它是|这是个|这是|是个|是种|是类|属于)\s*", "", clause).strip()
            clause = clause.strip("，,。；;：:、 ")
            if not clause or len(clause) < 4:
                continue
            if _is_weak_concept(clause):
                continue
            distilled.append(_trim_text(clause, max_length=24))
        return distilled[0] if distilled else ""

    points: list[str] = []
    for sentence in _pick_supporting_sentences(paragraph, concept, limit=4):
        cleaned = _distill_clause(sentence)
        if len(cleaned) < 4:
            continue
        if cleaned and cleaned not in points:
            points.append(cleaned)
    if len(points) < 2:
        for sentence in _split_sentences(paragraph):
            cleaned = _distill_clause(sentence)
            if len(cleaned) < 4:
                continue
            if cleaned and cleaned not in points:
                points.append(cleaned)
            if len(points) >= 4:
                break
    return points[:4]


def _build_explanation_from_segment(paragraph: str, concept: str, key_points: list[str]) -> str:
    focus = concept or "该知识点"
    ordered_points = "、".join(key_points[:3])
    has_mechanism = any(token in paragraph for token in ("机制", "调节", "反馈", "激活", "抑制", "导致", "通路"))
    has_comparison = any(token in paragraph for token in ("区别", "不同", "比较", "鉴别", "关系"))
    has_significance = any(token in paragraph for token in ("意义", "作用", "目的", "价值", "影响"))

    pieces = [f"本题真正考查的是{focus}。"]
    if ordered_points:
        pieces.append(f"参考答案至少要交代{ordered_points}。")
    if has_comparison:
        pieces.append("作答时应先分别点明判别标准，再落到核心区别或联系，不能只写“不同”而不给依据。")
        pieces.append("常见失分点是把相关概念混成一类，或只列现象没有写出判断抓手。")
    elif has_mechanism:
        pieces.append("答题主线应按“触发因素或条件→关键调节环节→最终生理或病理结果”展开，体现完整因果链。")
        pieces.append("易错点是只写结论，不写反馈方向、作用环节或调节目的。")
    elif has_significance:
        pieces.append("作答时不能停留在定义层面，还要写清它在本节过程中的作用位置以及为什么重要。")
        pieces.append("失分通常出在把“作用”写成空泛结论，没有落到具体生理或病理意义。")
    else:
        pieces.append("建议按“定义或特征→核心要点→生理意义或应用”的顺序组织答案，这样更容易拿到步骤分。")
        pieces.append("常见问题是只抄原句、不重组答案，导致要点不完整。")
    if _spoken_noise_score(paragraph) > 0:
        pieces.append("如果原文带有课堂口语，书写答案时要主动改成书面医学表述。")
    return _trim_text("".join(pieces), max_length=220)


def _explanation_is_low_quality(
    explanation: str,
    *,
    prompt: str = "",
    reference_answer: str = "",
    key_points: list[str] | None = None,
    source_excerpt: str = "",
) -> bool:
    text = _clean_fragment(explanation)
    reference = _clean_fragment(reference_answer)
    excerpt = _clean_fragment(source_excerpt)
    points = [_clean_fragment(point) for point in list(key_points or []) if _clean_fragment(point)]
    if len(text) < 80:
        return True
    if _spoken_noise_score(text) >= 2:
        return True
    if len(text) <= 120 and any(snippet in text for snippet in GENERIC_EXPLANATION_SNIPPETS):
        return True
    if reference and _sequence_ratio(text, reference) > 0.88:
        return True
    if excerpt and len(excerpt) >= 24 and _sequence_ratio(text, excerpt) > 0.82:
        return True
    guide_hits = sum(1 for marker in EXPLANATION_GUIDE_MARKERS if marker in text)
    causal_hits = sum(1 for marker in EXPLANATION_CAUSAL_MARKERS if marker in text)
    key_point_hits = sum(1 for point in points[:3] if point and point in text)
    prompt_focus = _extract_prompt_focus(prompt)
    focus_hit = bool(prompt_focus and prompt_focus in text)
    if guide_hits == 0:
        return True
    if not focus_hit and key_point_hits == 0 and len(text) < 120:
        return True
    if guide_hits < 2 and causal_hits == 0 and key_point_hits == 0:
        return True
    return False


def _build_source_excerpt_from_segment(paragraph: str, concept: str) -> str:
    sentences = _pick_supporting_sentences(paragraph, concept, limit=1)
    if not sentences:
        sentences = _split_sentences(paragraph)[:1]
    excerpt = sentences[0] if sentences else _polish_source_sentence(paragraph)
    return _trim_text(excerpt, max_length=120)


def _extract_focus_candidates(text: str, *, limit: int) -> list[str]:
    candidates: list[str] = []
    for item in _extract_topic_hints(text):
        cleaned = _clean_concept_candidate(item)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
        if len(candidates) >= limit:
            return candidates[:limit]
    for item in _extract_medical_terms(text, limit=max(limit * 2, limit)):
        cleaned = _clean_concept_candidate(item)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
        if len(candidates) >= limit:
            return candidates[:limit]
    for match in re.finditer(
        r"[\u4e00-\u9fffA-Za-z]{2,18}(?:正反馈|负反馈|前馈|稳态|激酶|酶|激素|受体|系统|机制|通路|血液凝固|血小板|"
        r"纤维蛋白|心力衰竭|休克|胆汁|胰液)",
        str(text or ""),
    ):
        cleaned = _clean_concept_candidate(match.group(0))
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def _pick_best_paragraph(paragraphs: list[str], focus: str, *, used_indices: set[int]) -> tuple[int, str]:
    best_row: tuple[int, int, int, str] | None = None
    for index, paragraph in enumerate(paragraphs):
        score = 0
        if focus and focus in paragraph:
            score += 6
        score += min(len(_pick_supporting_sentences(paragraph, focus, limit=2)), 2) * 2
        score += min(len(_extract_focus_candidates(paragraph, limit=3)), 3)
        score -= min(_spoken_noise_score(paragraph), 3) * 2
        if index not in used_indices:
            score += 2
        row = (score, index not in used_indices, -abs(len(paragraph) - 220), paragraph)
        if best_row is None or row[:3] > best_row[:3]:
            best_row = row
    if best_row is None:
        return 0, paragraphs[0]
    paragraph = best_row[3]
    return paragraphs.index(paragraph), paragraph


def _prompt_has_weak_focus(prompt: str) -> bool:
    text = _clean_fragment(prompt).rstrip("？?")
    if not text:
        return True
    if re.match(r"^请围绕(?:会|能|可|是|有|把|让)", text):
        return True
    if any(token in text for token in ("答题抓手", "关键环节", "核心内涵")) and any(token in text for token in ("它是个", "一下", "我们的前馈")):
        return True
    if any(text.startswith(prefix) for prefix in WEAK_CONCEPT_PREFIXES):
        return True
    patterns = (
        r"^(?:请(?:说明|概述|简述|概括|比较|结合原文说明|结合原文回答)?)(?P<focus>.+?)(?:的(?:核心机制|机制|主要特点|作用|意义|定义|处理要点|关键区别)|与.+?的(?:区别|关系)|是什么|有哪些|如何|为何)",
        r"^(?P<focus>.+?)(?:的(?:核心机制|机制|主要特点|作用|意义|定义|处理要点|关键区别)|与.+?的(?:区别|关系)|是什么|有哪些|如何|为何)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match and _is_weak_concept(match.group("focus")):
            return True
    return False


def _key_point_is_low_quality(point: str) -> bool:
    text = _polish_source_sentence(point)
    if len(text) < 4 or len(text) > 28:
        return True
    if any(marker in text for marker in ("下一句话", "这句话", "要是没有", "比如说", "天天说", "这个时候", "你说它")):
        return True
    if any(token in text for token in ("，", "；", "。")):
        return True
    if _spoken_noise_score(text) > 0:
        return True
    return False


def _choose_fallback_prompt(concept: str, paragraph: str) -> str:
    focus = concept or "该知识点"
    if focus.endswith("的定义"):
        return f"请简述{focus}，并说明答题时应覆盖的关键要点。"
    if focus.endswith("的特点"):
        return f"请概括{focus}，并指出作答时需要覆盖的核心方面。"
    if focus.endswith("的作用"):
        return f"请说明{focus}，并结合原文交代其生理或病理意义。"
    if any(token in paragraph for token in ("机制", "通路", "激活", "抑制", "反馈", "调节")):
        return f"{focus}的核心机制是什么？请结合原文说明关键环节。"
    if any(token in paragraph for token in ("区别", "不同", "比较", "鉴别")):
        return f"请概括{focus}与相关概念的关键区别，并指出答题抓手。"
    if any(token in paragraph for token in ("作用", "意义", "功能", "影响")):
        return f"{focus}在该生理或病理过程中起什么作用？请结合原文作答。"
    if any(token in paragraph for token in ("特点", "特征", "表现")):
        return f"请概括{focus}的主要特点，并说明答题时应覆盖哪些要点。"
    return f"请简述{focus}的定义、关键要点或临床意义。"


def _build_prompt_variants(concept: str, paragraph: str) -> list[str]:
    focus = concept or "该知识点"
    variants = [
        _choose_fallback_prompt(focus, paragraph),
        f"请概括{focus}的核心要点，并说明常见失分点。",
        f"请结合原文分析{focus}与本节主线知识点的联系。",
        f"请说明{focus}在本节知识结构中的定位和作答重点。",
    ]
    if any(token in paragraph for token in ("机制", "反馈", "调节", "激活", "抑制")):
        variants.append(f"请说明{focus}的调节方向、关键环节及最终结果。")
    if any(token in paragraph for token in ("意义", "目的", "作用", "影响")):
        variants.append(f"请概括{focus}的主要意义，并说明为什么这是本节重点。")
    deduped: list[str] = []
    for variant in variants:
        cleaned = _normalize_text(variant).strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _extract_prompt_focus(prompt: str) -> str:
    text = _clean_fragment(prompt).rstrip("？?")
    text = re.sub(r"\s*[（(](?:版本|题|延展)\s*\d+[）)]\s*$", "", text).strip()
    if not text:
        return ""
    patterns = (
        r"^(?:请(?:说明|概述|简述|概括|比较|分析|归纳|结合原文说明|结合原文回答)?)(?P<focus>.+?)(?:的(?:核心机制|机制|主要特点|作用|意义|定义|处理要点|关键区别)|与.+?的(?:区别|关系)|是什么|有哪些|如何|为何)",
        r"^(?P<focus>.+?)(?:的(?:核心机制|机制|主要特点|作用|意义|定义|处理要点|关键区别)|与.+?的(?:区别|关系)|是什么|有哪些|如何|为何)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        focus = _clean_concept_candidate(match.group("focus"))
        focus = re.sub(r"^(?:请|简述|概述|概括|分析|说明|理解|如何理解|请说明|请概括|请结合原文说明|请结合原文分析)+", "", focus).strip()
        focus = re.sub(r"(?:及其|才是|绝对不|的识别|在机体中的|在本节知识结构中的定位)$", "", focus).strip()
        if focus:
            return focus
    english_patterns = (
        r"^(?:Explain|Describe|Summarize|Outline|Discuss|Compare|Clarify|State|Identify|List)\s+(?P<focus>.+?)(?:[?.]|$)",
        r"^(?:Why|How)\s+(?P<focus>.+?)(?:[?.]|$)",
    )
    for pattern in english_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        focus = _clean_concept_candidate(match.group("focus"))
        if focus:
            return focus
    return _clean_concept_candidate(text)


def _build_concise_review_summary(source_text: str, chapter_title: str) -> str:
    focus = _chapter_focus_from_unit_title(chapter_title)
    summary_sentences: list[str] = []
    for paragraph in _extract_segments(source_text):
        for sentence in _pick_supporting_sentences(paragraph, focus, limit=2):
            cleaned = _polish_source_sentence(sentence)
            if len(cleaned) < 12 or _spoken_noise_score(cleaned) > 1 or cleaned in summary_sentences:
                continue
            summary_sentences.append(cleaned)
            if len(summary_sentences) >= 3 or len("；".join(summary_sentences)) >= 180:
                break
        if len(summary_sentences) >= 3 or len("；".join(summary_sentences)) >= 180:
            break
    if not summary_sentences:
        for sentence in _split_sentences(source_text):
            if len(sentence) < 12 or _spoken_noise_score(sentence) > 1 or sentence in summary_sentences:
                continue
            summary_sentences.append(sentence)
            if len(summary_sentences) >= 2:
                break
    return _trim_text("；".join(summary_sentences), max_length=180)


def _resolve_review_summary(summary: str, *, source_text: str, chapter_title: str) -> str:
    cleaned_summary = _trim_text(_polish_source_sentence(summary), max_length=180)
    if cleaned_summary and str(summary or "").strip().endswith("。") and not cleaned_summary.endswith("。"):
        cleaned_summary = f"{cleaned_summary}。"
    if len(cleaned_summary) >= 10 and _spoken_noise_score(cleaned_summary) <= 1:
        return cleaned_summary
    return _build_concise_review_summary(source_text, chapter_title)


def _normalize_review_concept_name(value: Any) -> str:
    text = _clean_concept_candidate(str(value or ""))
    text = re.sub(r"^(?:请|简述|概述|概括|分析|说明|理解|如何理解|围绕|结合原文说明|结合原文分析)+", "", text).strip()
    text = re.sub(r"(?:及其|才是|绝对不|的识别|在机体中的|在本节知识结构中的定位)$", "", text).strip()
    text = re.sub(r"(?:相关要点|关键点|核心内容|重点内容|原理要点)$", "", text).strip()
    return text


def _review_question_axis_from_text(text: str) -> str:
    content = str(text or "")
    if any(token in content for token in ("机制", "通路", "激活", "抑制", "反馈", "调节")):
        return "mechanism"
    if any(token in content for token in ("区别", "不同", "比较", "鉴别")):
        return "comparison"
    if any(token in content for token in ("作用", "意义", "目的", "影响", "价值")):
        return "significance"
    if any(token in content for token in ("特点", "特征", "表现", "分类", "分型")):
        return "features"
    return "definition"


def _chapter_concept_candidates(chapter: Optional[Chapter], *, source_text: str, summary: str, chapter_title: str) -> list[str]:
    candidates: list[str] = []
    chapter_focus = _chapter_focus_from_unit_title(chapter_title)
    if chapter_focus and chapter_focus not in candidates:
        candidates.append(chapter_focus)
    if chapter is not None:
        for item in list(getattr(chapter, "concepts", None) or []):
            if isinstance(item, dict):
                name = _normalize_review_concept_name(item.get("name") or item.get("concept_name"))
            else:
                name = _normalize_review_concept_name(item)
            if name and name not in candidates:
                candidates.append(name)
    for text in (summary, chapter_title, source_text):
        for candidate in _extract_focus_candidates(text, limit=16):
            cleaned = _normalize_review_concept_name(candidate)
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
    if not candidates:
        for match in re.finditer(
            r"([\u4e00-\u9fffA-Za-z]{2,16})的(?:定义|诱因|机制|作用|意义|特点|治疗|处理|处理策略|分型|分类|表现|联系)",
            " ".join(part for part in [chapter_title, summary, source_text] if part),
        ):
            cleaned = _normalize_review_concept_name(match.group(1))
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def _score_review_concept(name: str, *, source_text: str, summary: str, chapter_title: str) -> float:
    normalized_name = _normalize_match_key(name)
    if not normalized_name:
        return 0.0
    score = 0.0
    source_key = _normalize_match_key(source_text)
    summary_key = _normalize_match_key(summary)
    title_key = _normalize_match_key(chapter_title)
    if normalized_name and normalized_name in source_key:
        score += 3.0
        score += min(source_key.count(normalized_name), 3) * 0.8
    if normalized_name and normalized_name in summary_key:
        score += 2.0
    if normalized_name and normalized_name in title_key:
        score += 1.2
    score += _sequence_ratio(name, chapter_title)
    if re.search(r"(?:机制|作用|特点|调节|反馈|稳态|意义|分型|分类)$", name):
        score += 0.4
    return score


def _build_local_review_concept_blueprint(
    *,
    source_text: str,
    summary: str,
    chapter_title: str,
    concept_candidates: list[str],
    limit: int = 6,
) -> list[dict[str, Any]]:
    paragraphs = _extract_segments(source_text) or [source_text]
    ranked_candidates = sorted(
        concept_candidates,
        key=lambda candidate: (
            _score_review_concept(candidate, source_text=source_text, summary=summary, chapter_title=chapter_title),
            len(candidate),
        ),
        reverse=True,
    )

    blueprint: list[dict[str, Any]] = []
    used_focuses: set[str] = set()
    used_paragraph_indices: set[int] = set()
    for concept_name in ranked_candidates:
        focus = _normalize_review_concept_name(concept_name)
        if not focus or focus in used_focuses or _is_weak_concept(focus):
            continue
        paragraph_index, paragraph = _pick_best_paragraph(paragraphs, focus, used_indices=used_paragraph_indices)
        key_points = _build_key_points_from_segment(paragraph, focus)
        if len(key_points) < 2:
            continue
        used_focuses.add(focus)
        used_paragraph_indices.add(paragraph_index)
        blueprint.append(
            {
                "concept_name": focus,
                "prompt_focus": focus,
                "question_axis": _review_question_axis_from_text(paragraph),
                "source_excerpt": _build_source_excerpt_from_segment(paragraph, focus),
                "supporting_text": paragraph,
                "expected_key_points": key_points[:4],
                "reference_answer": _build_reference_answer_from_segment(paragraph, focus),
                "explanation_hint": _build_explanation_from_segment(paragraph, focus, key_points[:4]),
                "priority": int(round(_score_review_concept(focus, source_text=source_text, summary=summary, chapter_title=chapter_title) * 10)),
            }
        )
        if len(blueprint) >= limit:
            break
    return blueprint


def _normalize_blueprint_question_axis(value: Any) -> str:
    axis = str(value or "").strip().lower()
    if axis in {"definition", "mechanism", "comparison", "significance", "features"}:
        return axis
    return "definition"


def _normalize_review_concept_blueprint(
    raw_items: list[dict[str, Any]],
    *,
    source_text: str,
    chapter_title: str,
    summary: str,
    fallback_blueprint: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    paragraphs = _extract_segments(source_text) or [source_text]
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    fallback_by_focus = {
        _normalize_match_key(item.get("concept_name") or item.get("prompt_focus")): item
        for item in fallback_blueprint
        if _normalize_match_key(item.get("concept_name") or item.get("prompt_focus"))
    }

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        concept_name = _normalize_review_concept_name(item.get("concept_name") or item.get("prompt_focus"))
        if not concept_name or _is_weak_concept(concept_name):
            continue
        key = _normalize_match_key(concept_name)
        if not key or key in seen:
            continue
        seen.add(key)

        supporting_text = _clean_fragment(item.get("source_excerpt") or "")
        if supporting_text and len(supporting_text) >= 12:
            paragraph = supporting_text
        else:
            _, paragraph = _pick_best_paragraph(paragraphs, concept_name, used_indices=set())

        key_points = [
            _trim_text(_polish_source_sentence(point), max_length=28)
            for point in list(item.get("expected_key_points") or item.get("key_points") or [])
            if _polish_source_sentence(point)
        ]
        key_points = [point for point in key_points if point and not _key_point_is_low_quality(point)]
        if len(key_points) < 2:
            key_points = _build_key_points_from_segment(paragraph, concept_name)
        fallback_item = fallback_by_focus.get(key)
        normalized.append(
            {
                "concept_name": concept_name,
                "prompt_focus": _normalize_review_concept_name(item.get("prompt_focus") or concept_name) or concept_name,
                "question_axis": _normalize_blueprint_question_axis(item.get("question_axis") or item.get("question_type")),
                "source_excerpt": _build_source_excerpt_from_segment(paragraph, concept_name),
                "supporting_text": paragraph,
                "expected_key_points": key_points[:4],
                "reference_answer": _trim_text(
                    _polish_source_sentence(item.get("reference_answer") or ""),
                    max_length=180,
                ) or (fallback_item or {}).get("reference_answer") or _build_reference_answer_from_segment(paragraph, concept_name),
                "explanation_hint": _trim_text(
                    _polish_source_sentence(item.get("explanation_hint") or item.get("selection_reason") or ""),
                    max_length=220,
                ) or (fallback_item or {}).get("explanation_hint") or _build_explanation_from_segment(paragraph, concept_name, key_points[:4]),
                "priority": int(item.get("priority") or (fallback_item or {}).get("priority") or 0),
            }
        )
        if len(normalized) >= limit:
            break

    if len(normalized) < limit:
        for fallback_item in fallback_blueprint:
            key = _normalize_match_key(fallback_item.get("concept_name") or fallback_item.get("prompt_focus"))
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(dict(fallback_item))
            if len(normalized) >= limit:
                break
    return normalized[:limit]


async def _ai_refine_review_concept_blueprint(
    *,
    source_text: str,
    summary: str,
    chapter_title: str,
    chapter_concepts: list[str],
    fallback_blueprint: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, Any]]:
    prompt = f"""你是医学课程知识点设计专家。请基于章节摘要、候选知识点和原文内容，筛选出最值得命题的 {limit} 个知识点，并为每个知识点指定命题方向。

【章节】{chapter_title}
【摘要】{summary or "无"}
【候选知识点】
{", ".join(chapter_concepts[:18]) or "无"}

【已有本地提炼蓝图】
{json.dumps(fallback_blueprint[:limit], ensure_ascii=False, indent=2)}

【原文内容】
{source_text}

要求：
1. 只保留真正适合作为复习题核心的知识点，不要输出口语碎片、代词、课堂过渡语。
2. concept_name 必须是清晰的医学知识点名称。
3. prompt_focus 可以比 concept_name 更具体，但仍需是书面化考点。
4. question_axis 只能是 definition / mechanism / comparison / significance / features 之一。
5. expected_key_points 保留 2-4 条，都是可判分的内容要点。
6. 尽量覆盖整章里不同的核心知识点，避免把多个名额浪费在同一知识点的近义重复表达上。
7. source_excerpt 必须摘自原文关键句，便于定位。
8. 只返回 JSON。
"""
    schema = {
        "concepts": [
            {
                "concept_name": "知识点名称",
                "prompt_focus": "更具体的命题焦点",
                "question_axis": "mechanism",
                "source_excerpt": "原文关键句",
                "expected_key_points": ["要点1", "要点2"],
                "selection_reason": "为什么这个知识点值得命题",
                "priority": 10,
            }
        ]
    }
    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=2400,
        temperature=0.15,
        timeout=70,
        use_heavy=True,
    )
    return list(result.get("concepts") or [])[:limit]


async def _build_review_generation_context(
    db: Session,
    *,
    task: ChapterReviewTask,
    source_text: str,
    summary: str,
) -> dict[str, Any]:
    chapter = db.query(Chapter).filter(Chapter.id == task.review_chapter.chapter_id).first()
    chapter_title = task.review_chapter.chapter_title
    unit_source_text = clean_review_content(source_text)
    chapter_source_text = _task_chapter_source_text(task)
    polished_summary = ""
    polished_notes: list[dict[str, Any]] = []
    concept_limit = min(
        GENERATION_BLUEPRINT_MAX_ITEMS,
        max(int(task.question_count or QUESTIONS_PER_REVIEW_UNIT) + 4, 10),
    )
    chapter_concepts = _chapter_concept_candidates(
        chapter,
        source_text=chapter_source_text,
        summary=summary,
        chapter_title=chapter_title,
    )
    local_blueprint = _build_local_review_concept_blueprint(
        source_text=chapter_source_text,
        summary=summary,
        chapter_title=chapter_title,
        concept_candidates=chapter_concepts,
        limit=concept_limit,
    )
    if not local_blueprint:
        fallback_candidates = _extract_focus_candidates(chapter_source_text, limit=max(concept_limit * 2, 12))
        chapter_focus = _chapter_focus_from_unit_title(chapter_title)
        if chapter_focus and chapter_focus not in fallback_candidates:
            fallback_candidates.insert(0, chapter_focus)
        local_blueprint = _build_local_review_concept_blueprint(
            source_text=chapter_source_text,
            summary=summary,
            chapter_title=chapter_title,
            concept_candidates=fallback_candidates,
            limit=concept_limit,
        )

    ai_blueprint: list[dict[str, Any]] = []
    try:
        ai_blueprint = await asyncio.wait_for(
            _ai_refine_review_concept_blueprint(
                source_text=chapter_source_text,
                summary=summary,
                chapter_title=chapter_title,
                chapter_concepts=chapter_concepts,
                fallback_blueprint=local_blueprint,
                limit=concept_limit,
            ),
            timeout=22,
        )
    except Exception:
        ai_blueprint = []

    concept_blueprint = _normalize_review_concept_blueprint(
        ai_blueprint,
        source_text=chapter_source_text,
        chapter_title=chapter_title,
        summary=summary,
        fallback_blueprint=local_blueprint,
        limit=concept_limit,
    )
    if _material_needs_polish(chapter_source_text):
        try:
            polished_result = await asyncio.wait_for(
                _ai_polish_review_source(
                    chapter_title=chapter_title,
                    summary=summary,
                    chapter_concepts=chapter_concepts,
                    source_text=chapter_source_text,
                ),
                timeout=36,
            )
            polished_summary = _trim_text(
                _polish_source_sentence(str(polished_result.get("summary") or "")),
                max_length=180,
            )
            for item in list(polished_result.get("notes") or []):
                if not isinstance(item, dict):
                    continue
                concept_name = _normalize_review_concept_name(item.get("concept_name"))
                note = _trim_text(_polish_source_sentence(str(item.get("note") or "")), max_length=140)
                source_excerpt = _trim_text(_polish_source_sentence(str(item.get("source_excerpt") or "")), max_length=120)
                if not concept_name or _is_weak_concept(concept_name) or not note:
                    continue
                polished_notes.append(
                    {
                        "concept_name": concept_name,
                        "note": note,
                        "source_excerpt": source_excerpt,
                    }
                )
                if len(polished_notes) >= concept_limit:
                    break
        except Exception:
            polished_summary = ""
            polished_notes = []

    raw_generation_source_text = _build_generation_scope_text(
        unit_source_text=unit_source_text,
        chapter_source_text=chapter_source_text,
        blueprint=concept_blueprint or local_blueprint,
    )
    polished_note_text = _format_polished_material_notes(polished_notes)
    generation_source_text = raw_generation_source_text
    if polished_note_text:
        generation_source_text = f"书面化复习笔记：\n{polished_note_text}\n\n原文关键材料：\n{raw_generation_source_text}".strip()
    return {
        "chapter_title": chapter_title,
        "summary": polished_summary or summary,
        "chapter_concepts": chapter_concepts[:16],
        "concept_blueprint": concept_blueprint,
        "unit_source_text": unit_source_text,
        "chapter_source_text": chapter_source_text,
        "generation_source_text": generation_source_text,
        "polished_notes": polished_notes,
    }


def _review_generation_context_data() -> dict[str, Any]:
    return dict(_review_generation_context.get() or {})


def _build_blueprint_text(blueprint: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(blueprint, start=1):
        key_points = "；".join(list(item.get("expected_key_points") or [])[:4])
        lines.extend(
            [
                f"{index}. 知识点：{item.get('concept_name')}",
                f"   命题焦点：{item.get('prompt_focus') or item.get('concept_name')}",
                f"   命题方向：{item.get('question_axis') or 'definition'}",
                f"   原文定位：{item.get('source_excerpt') or ''}",
                f"   得分点：{key_points}",
            ]
        )
    return "\n".join(lines)


def _material_needs_polish(source_text: str) -> bool:
    raw_text = str(source_text or "")
    text = clean_review_content(source_text)
    if not text:
        return False
    raw_sample = raw_text[:2600]
    sample = text[:2600]
    if len(re.findall(r"(?:我们的|这个时候|你看|对吧|按理来说|一般来讲|所以|那么)", raw_sample)) >= 4:
        return True
    if _spoken_noise_score(sample) >= GENERATION_POLISH_TRIGGER_NOISE_SCORE:
        return True
    if len(re.findall(r"(?:我们的|这个时候|你看|对吧|按理来说|一般来讲|所以|那么)", sample)) >= 6:
        return True
    suspicious_sentences = 0
    for sentence in _split_sentences(sample):
        stripped = sentence.strip()
        if len(stripped) < 10:
            suspicious_sentences += 1
            continue
        if re.match(r"^(?:如果|但是|不过|所以|那么|一下|这个时候|我们的|你看|对吧)", stripped):
            suspicious_sentences += 1
            continue
        if stripped.startswith("它是") or stripped.startswith("这是"):
            suspicious_sentences += 1
            continue
    return suspicious_sentences >= 4


def _format_polished_material_notes(notes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(notes, start=1):
        concept_name = str(item.get("concept_name") or "").strip() or f"要点 {index}"
        note = str(item.get("note") or "").strip()
        source_excerpt = str(item.get("source_excerpt") or "").strip()
        if not note:
            continue
        lines.append(f"{index}. {concept_name}：{note}")
        if source_excerpt:
            lines.append(f"   原文定位：{source_excerpt}")
    return "\n".join(lines)


async def _ai_polish_review_source(
    *,
    chapter_title: str,
    summary: str,
    chapter_concepts: list[str],
    source_text: str,
) -> dict[str, Any]:
    prompt = f"""你是医学课程讲义编辑。请把下面夹杂课堂口语、ASR 残句和重复表达的原始材料，整理成适合出题的书面化复习笔记。
【章节】{chapter_title}
【已有摘要】{summary or "无"}
【候选知识点】{", ".join(chapter_concepts[:18]) or "无"}
【原始材料】
{_trim_text(source_text, max_length=GENERATION_POLISH_SOURCE_MAX_CHARS)}

要求：
1. 只保留原文中明确出现并能直接支持出题的医学事实，不得引入材料外知识。
2. `summary` 输出 80-180 字的书面化摘要。
3. `notes` 输出 6-12 条结构化笔记。每条都要包含：
   - `concept_name`：明确知识点名称，不能是“它是个正反馈”“这个时候”这种碎片
   - `note`：60-120 字书面化表述，尽量去掉课堂口语、重复和残句
   - `source_excerpt`：对应的原文关键句，便于回看定位
4. 优先覆盖不同知识点，避免多条笔记围绕同一小句反复改写。
5. 只返回 JSON。"""
    schema = {
        "summary": "书面化摘要",
        "notes": [
            {
                "concept_name": "知识点名称",
                "note": "书面化复习笔记",
                "source_excerpt": "原文关键句",
            }
        ],
    }
    return await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=2600,
        temperature=0.1,
        timeout=80,
        use_heavy=True,
    )


def _build_generation_material(unit: ChapterReviewUnit, *, summary: str, chapter_title: str) -> dict[str, Any]:
    context = _review_generation_context_data()
    unit_source_text = clean_review_content(
        context.get("unit_source_text") or unit.cleaned_text or unit.raw_text or unit.excerpt or unit.unit_title
    )
    chapter_source_text = clean_review_content(context.get("chapter_source_text") or unit_source_text)
    source_text = clean_review_content(context.get("generation_source_text") or chapter_source_text or unit_source_text)
    polished_notes = list(context.get("polished_notes") or [])
    resolved_chapter_title = str(context.get("chapter_title") or chapter_title or unit.unit_title).strip() or unit.unit_title
    resolved_summary = _resolve_review_summary(
        str(context.get("summary") or summary or ""),
        source_text=chapter_source_text or source_text,
        chapter_title=resolved_chapter_title,
    )
    concept_blueprint = list(context.get("concept_blueprint") or [])
    chapter_concepts = [str(item) for item in list(context.get("chapter_concepts") or []) if str(item).strip()]
    chapter_focus = _chapter_focus_from_unit_title(resolved_chapter_title or unit.unit_title)
    if chapter_focus and chapter_focus not in chapter_concepts:
        chapter_concepts.insert(0, chapter_focus)
    focus_candidates: list[str] = []
    for item in concept_blueprint:
        for candidate in (item.get("concept_name"), item.get("prompt_focus")):
            cleaned = _normalize_review_concept_name(candidate)
            if cleaned and cleaned not in focus_candidates:
                focus_candidates.append(cleaned)
    for item in polished_notes:
        cleaned = _normalize_review_concept_name(item.get("concept_name"))
        if cleaned and cleaned not in focus_candidates:
            focus_candidates.append(cleaned)
    for candidate in chapter_concepts:
        cleaned = _normalize_review_concept_name(candidate)
        if cleaned and cleaned not in focus_candidates:
            focus_candidates.append(cleaned)
    for text in (resolved_summary, resolved_chapter_title, chapter_source_text, unit_source_text):
        for candidate in _extract_focus_candidates(text, limit=12):
            cleaned = _clean_concept_candidate(candidate)
            if cleaned and cleaned not in focus_candidates:
                focus_candidates.append(cleaned)
            if len(focus_candidates) >= 16:
                break
        if len(focus_candidates) >= 16:
            break
    if chapter_focus and chapter_focus not in focus_candidates:
        focus_candidates.insert(0, chapter_focus)

    digest_sentences: list[str] = []
    for item in polished_notes:
        note = _trim_text(_polish_source_sentence(str(item.get("note") or "")), max_length=140)
        if note and note not in digest_sentences:
            digest_sentences.append(note)
        if len(digest_sentences) >= 8 or len("；".join(digest_sentences)) >= 520:
            break
    digest_paragraphs = _extract_segments(chapter_source_text or source_text)
    digest_focuses = focus_candidates[:4] or ([chapter_focus] if chapter_focus else [])
    for focus in digest_focuses:
        for paragraph in digest_paragraphs:
            for sentence in _pick_supporting_sentences(paragraph, focus, limit=2):
                cleaned = _polish_source_sentence(sentence)
                if len(cleaned) < 12 or _spoken_noise_score(cleaned) > 1 or cleaned in digest_sentences:
                    continue
                digest_sentences.append(cleaned)
                if len(digest_sentences) >= 8 or len("；".join(digest_sentences)) >= 520:
                    break
            if len(digest_sentences) >= 8 or len("；".join(digest_sentences)) >= 520:
                break
        if len(digest_sentences) >= 8 or len("；".join(digest_sentences)) >= 520:
            break

    return {
        "source_text": source_text,
        "chapter_source_text": chapter_source_text,
        "unit_source_text": unit_source_text,
        "summary": resolved_summary,
        "focuses": focus_candidates,
        "focus_line": "、".join(focus_candidates[:8]),
        "digest": "；".join(digest_sentences[:8]),
        "concept_blueprint": concept_blueprint,
        "blueprint_text": _build_blueprint_text(concept_blueprint),
        "chapter_concepts": chapter_concepts,
        "chapter_title": resolved_chapter_title,
    }


def _pick_question_focus(prompt: str, source_excerpt: str, supporting_text: str, chapter_title: str) -> str:
    best_focus = ""
    best_score = -1
    for candidate in _extract_focus_candidates(" ".join([source_excerpt, supporting_text, chapter_title, prompt]), limit=14):
        cleaned = _clean_concept_candidate(candidate)
        if not cleaned or _is_weak_concept(cleaned):
            continue
        score = 0
        if cleaned and cleaned in prompt:
            score += 2
        if cleaned and cleaned in source_excerpt:
            score += 4
        if cleaned and cleaned in supporting_text:
            score += 3
        if cleaned and cleaned in chapter_title:
            score += 2
        if 2 <= len(cleaned) <= 10:
            score += 1
        if re.search(r"(?:机制|作用|特点|调节|反馈|稳态|凝固|排卵|分娩|血栓|调定点)$", cleaned):
            score += 1
        if score > best_score:
            best_focus = cleaned
            best_score = score
    return best_focus or _chapter_focus_from_unit_title(chapter_title) or "该知识点"


def _normalize_question_payload(item: dict[str, Any], *, unit: ChapterReviewUnit) -> dict[str, Any]:
    context = _review_generation_context_data()
    source_text = clean_review_content(
        context.get("chapter_source_text") or unit.cleaned_text or unit.raw_text or unit.excerpt or unit.unit_title
    )
    generation_text = clean_review_content(context.get("generation_source_text") or source_text)
    supporting_text = source_text if len(source_text) >= 80 else f"{source_text} {unit.excerpt or ''}".strip()
    if generation_text:
        supporting_text = generation_text if len(generation_text) >= 80 else supporting_text
    source_excerpt = _trim_text(_clean_fragment(str(item.get("source_excerpt") or "")), max_length=120)
    chapter_title = str(context.get("chapter_title") or unit.unit_title).strip() or unit.unit_title
    explicit_focus = _normalize_review_concept_name(item.get("prompt_focus") or item.get("concept_name"))
    if explicit_focus and not _is_weak_concept(explicit_focus):
        concept = explicit_focus
    else:
        concept = _pick_question_focus(
            str(item.get("prompt") or ""),
            source_excerpt,
            supporting_text,
            chapter_title,
        )
    paragraphs = _extract_segments(supporting_text) or [supporting_text]
    _, best_paragraph = _pick_best_paragraph(paragraphs, concept, used_indices=set())
    if not source_excerpt:
        source_excerpt = _build_source_excerpt_from_segment(best_paragraph, concept)
    reference_answer = _trim_text(_polish_source_sentence(str(item.get("reference_answer") or "")), max_length=180)
    if len(reference_answer) < 18:
        reference_answer = _build_reference_answer_from_segment(best_paragraph, concept)
    key_points = [
        _trim_text(_polish_source_sentence(point), max_length=28)
        for point in list(item.get("key_points") or [])
        if _polish_source_sentence(point)
    ]
    key_points = [point for point in key_points if point and not _key_point_is_low_quality(point)]
    if len(key_points) < 2:
        key_points = _build_key_points_from_segment(best_paragraph, concept)
    explanation = _trim_text(_polish_source_sentence(str(item.get("explanation") or "")), max_length=220)
    if _explanation_is_low_quality(
        explanation,
        prompt=str(item.get("prompt") or ""),
        reference_answer=reference_answer,
        key_points=key_points,
        source_excerpt=source_excerpt,
    ):
        explanation = _build_explanation_from_segment(best_paragraph, concept, key_points)
    prompt = _normalize_text(str(item.get("prompt") or ""))
    prompt = _strip_spoken_prefixes(prompt).strip("，,。；;：:、 ")
    prompt = re.sub(r"^(?:第?\s*\d+\s*题?|题目\s*\d+)\s*[：:、.．]\s*", "", prompt)
    prompt = re.sub(r"\s*[（(](?:版本|题|延展)\s*\d+[）)]\s*$", "", prompt).strip()
    if not prompt or _prompt_has_weak_focus(prompt) or _spoken_noise_score(prompt) > 0:
        prompt = _choose_fallback_prompt(concept, best_paragraph)
    prompt = prompt.rstrip("。；; ")
    if prompt and prompt[-1] not in {"？", "?"}:
        prompt += "？"
    question_axis = _question_axis_from_payload(
        {
            "prompt": prompt,
            "source_excerpt": source_excerpt,
            "reference_answer": reference_answer,
            "question_axis": item.get("question_axis"),
        }
    )
    payload = {
        "prompt": prompt,
        "concept_name": str(item.get("concept_name") or explicit_focus or concept),
        "prompt_focus": str(item.get("prompt_focus") or explicit_focus or concept),
        "reference_answer": reference_answer,
        "key_points": key_points[:4],
        "explanation": explanation,
        "source_excerpt": source_excerpt,
        "question_axis": question_axis,
        "priority": int(item.get("priority") or 0),
    }
    prompt_focus = _extract_prompt_focus(prompt)
    structured_payload_ok = bool(
        explicit_focus
        and not _is_weak_concept(explicit_focus)
        and (explicit_focus in payload["prompt"] or explicit_focus in payload["reference_answer"])
        and prompt_focus
        and not _is_weak_concept(prompt_focus)
        and len(payload["reference_answer"]) >= 24
        and len(payload["key_points"]) >= 2
        and len(payload["source_excerpt"]) >= 12
    )
    if _is_low_quality_question_payload(payload) and not structured_payload_ok:
        repaired_paragraph = best_paragraph
        repaired_focus = _pick_question_focus(payload["prompt"], source_excerpt, repaired_paragraph, chapter_title)
        repaired_key_points = _build_key_points_from_segment(repaired_paragraph, repaired_focus)
        payload = {
            "prompt": _choose_fallback_prompt(repaired_focus, repaired_paragraph),
            "concept_name": repaired_focus,
            "prompt_focus": repaired_focus,
            "reference_answer": _build_reference_answer_from_segment(repaired_paragraph, repaired_focus),
            "key_points": repaired_key_points[:4],
            "explanation": _build_explanation_from_segment(repaired_paragraph, repaired_focus, repaired_key_points),
            "source_excerpt": _build_source_excerpt_from_segment(repaired_paragraph, repaired_focus),
            "question_axis": _review_question_axis_from_text(
                " ".join([_choose_fallback_prompt(repaired_focus, repaired_paragraph), repaired_paragraph])
            ),
            "priority": int(item.get("priority") or 0),
        }
    return payload


def _is_low_quality_question_payload(item: dict[str, Any]) -> bool:
    prompt = _clean_fragment(str(item.get("prompt") or ""))
    reference_answer = _clean_fragment(str(item.get("reference_answer") or ""))
    explanation = _clean_fragment(str(item.get("explanation") or ""))
    key_points = [_clean_fragment(point) for point in list(item.get("key_points") or []) if _clean_fragment(point)]
    source_excerpt = _clean_fragment(str(item.get("source_excerpt") or ""))
    prompt_focus = _extract_prompt_focus(prompt)

    if not prompt or not reference_answer:
        return True
    if len(prompt) < 10 or len(prompt) > 72:
        return True
    if not prompt_focus or _is_weak_concept(prompt_focus):
        return True
    if _prompt_has_weak_focus(prompt):
        return True
    if re.match(r"^(?:那么|所以|对吧|好|然后|接下来|最后|另外|这就|那你|你看)", prompt):
        return True
    if _spoken_noise_score(prompt) > 0:
        return True
    min_reference_length = 12 if len(_clean_fragment(str(item.get("source_excerpt") or ""))) < 40 else 18
    if len(reference_answer) < min_reference_length:
        return True
    if _explanation_is_low_quality(
        explanation,
        prompt=prompt,
        reference_answer=reference_answer,
        key_points=key_points,
        source_excerpt=source_excerpt,
    ):
        return True
    if len(key_points) < 2:
        return True
    if any(_key_point_is_low_quality(point) for point in key_points):
        return True
    if source_excerpt and len(source_excerpt) < 12:
        return True
    if _spoken_noise_score(reference_answer) >= 2:
        return True
    if source_excerpt and len(source_excerpt) >= 32 and _sequence_ratio(reference_answer, source_excerpt) > 0.82:
        return True
    if source_excerpt and _spoken_noise_score(source_excerpt) >= 3 and _sequence_ratio(reference_answer, source_excerpt) > 0.55:
        return True
    if _sequence_ratio(prompt, reference_answer) > 0.66:
        return True
    return False


def _question_payload_quality_score(item: dict[str, Any]) -> int:
    prompt = _clean_fragment(str(item.get("prompt") or ""))
    reference_answer = _clean_fragment(str(item.get("reference_answer") or ""))
    explanation = _clean_fragment(str(item.get("explanation") or ""))
    source_excerpt = _clean_fragment(str(item.get("source_excerpt") or ""))
    key_points = [_clean_fragment(point) for point in list(item.get("key_points") or []) if _clean_fragment(point)]
    score = 0
    if _is_low_quality_question_payload(item):
        return score
    focus = _extract_prompt_focus(prompt)
    if focus and not _is_weak_concept(focus):
        score += 20
    explicit_focus = _normalize_review_concept_name(item.get("prompt_focus") or item.get("concept_name"))
    if explicit_focus and not _is_weak_concept(explicit_focus):
        score += 10
    score += min(len(key_points), 4) * 8
    if 60 <= len(reference_answer) <= 180:
        score += 20
    elif len(reference_answer) >= 36:
        score += 10
    if 110 <= len(explanation) <= 220:
        score += 22
    elif len(explanation) >= 90:
        score += 12
    if 20 <= len(source_excerpt) <= 120:
        score += 12
    score += min(max(int(item.get("priority") or 0), 0), 16)
    score += max(0, 15 - int(_sequence_ratio(prompt, reference_answer) * 20))
    return score


def _question_focus_key(item: dict[str, Any]) -> str:
    explicit_focus = _normalize_review_concept_name(item.get("prompt_focus") or item.get("concept_name"))
    if explicit_focus:
        return _normalize_match_key(explicit_focus)
    prompt = str(item.get("prompt") or "")
    focus = _extract_prompt_focus(prompt)
    if focus:
        return _normalize_match_key(focus)
    source_excerpt = _clean_fragment(str(item.get("source_excerpt") or ""))
    if source_excerpt:
        return _normalize_match_key(source_excerpt[:24])
    return ""


def _question_axis_from_payload(item: dict[str, Any]) -> str:
    explicit_axis = str(item.get("question_axis") or "").strip().lower()
    if explicit_axis in {"definition", "mechanism", "comparison", "significance", "features"}:
        return explicit_axis
    combined = " ".join(
        part
        for part in (
            str(item.get("prompt") or ""),
            str(item.get("source_excerpt") or ""),
            str(item.get("reference_answer") or ""),
        )
        if str(part or "").strip()
    )
    return _review_question_axis_from_text(combined)


def _question_semantic_key(item: dict[str, Any]) -> str:
    focus_key = _question_focus_key(item)
    axis = _question_axis_from_payload(item)
    if focus_key:
        return f"{focus_key}|{axis}"
    prompt_key = _normalize_match_key(str(item.get("prompt") or ""))
    return f"{prompt_key[:32]}|{axis}"


def _question_source_key(item: dict[str, Any]) -> str:
    excerpt = _clean_fragment(str(item.get("source_excerpt") or ""))
    if excerpt:
        return _normalize_match_key(excerpt[:48])
    answer = _clean_fragment(str(item.get("reference_answer") or ""))
    if answer:
        return _normalize_match_key(answer[:48])
    return ""


def _question_payloads_are_redundant(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_focus = _question_focus_key(left)
    right_focus = _question_focus_key(right)
    left_axis = _question_axis_from_payload(left)
    right_axis = _question_axis_from_payload(right)
    prompt_ratio = _sequence_ratio(str(left.get("prompt") or ""), str(right.get("prompt") or ""))
    answer_ratio = _sequence_ratio(str(left.get("reference_answer") or ""), str(right.get("reference_answer") or ""))
    excerpt_ratio = _sequence_ratio(str(left.get("source_excerpt") or ""), str(right.get("source_excerpt") or ""))
    left_source = _question_source_key(left)
    right_source = _question_source_key(right)

    if left_focus and right_focus and left_focus == right_focus and left_axis == right_axis:
        if prompt_ratio >= 0.7 or answer_ratio >= 0.78 or excerpt_ratio >= 0.78:
            return True
    if left_focus and right_focus and left_focus == right_focus:
        if prompt_ratio >= 0.84:
            return True
        if answer_ratio >= 0.9 and excerpt_ratio >= 0.72:
            return True
    if left_source and right_source and left_source == right_source:
        if prompt_ratio >= 0.35 or answer_ratio >= 0.72 or excerpt_ratio >= 0.9:
            return True
    return False


def _question_batch_diversity_stats(questions: list[dict[str, Any]]) -> tuple[set[str], set[str], int]:
    focus_counts: dict[str, int] = {}
    semantic_keys: set[str] = set()
    for item in questions:
        focus_key = _question_focus_key(item)
        semantic_key = _question_semantic_key(item)
        if focus_key:
            focus_counts[focus_key] = focus_counts.get(focus_key, 0) + 1
        if semantic_key:
            semantic_keys.add(semantic_key)
    max_focus_frequency = max(focus_counts.values(), default=0)
    return set(focus_counts.keys()), semantic_keys, max_focus_frequency


def _prepare_questions_for_storage(
    raw_questions: list[dict[str, Any]],
    *,
    unit: ChapterReviewUnit,
    question_count: int,
) -> list[dict[str, Any]]:
    normalized_rows: list[tuple[int, int, str, str, str, str, dict[str, Any]]] = []
    seen_prompt_keys: set[str] = set()
    for item in raw_questions:
        normalized = _normalize_question_payload(item, unit=unit)
        prompt_key = _normalize_match_key(normalized.get("prompt") or "")
        if not prompt_key or prompt_key in seen_prompt_keys:
            continue
        seen_prompt_keys.add(prompt_key)
        focus_key = _question_focus_key(normalized)
        semantic_key = _question_semantic_key(normalized)
        source_key = _question_source_key(normalized)
        quality = _question_payload_quality_score(normalized)
        priority = int(normalized.get("priority") or 0)
        normalized_rows.append((priority, quality, focus_key, semantic_key, source_key, prompt_key, normalized))

    normalized_rows.sort(key=lambda row: (row[0], row[1], len(row[2]), len(row[3]), row[5]), reverse=True)

    prepared: list[dict[str, Any]] = []
    seen_prompts: set[str] = set()
    used_focuses: set[str] = set()
    used_semantic_keys: set[str] = set()
    used_source_keys: set[str] = set()

    def try_add(
        normalized: dict[str, Any],
        *,
        focus_key: str,
        semantic_key: str,
        source_key: str,
        prompt_key: str,
        enforce_unique_focus: bool,
        enforce_unique_semantic: bool,
        enforce_unique_source: bool,
    ) -> bool:
        if prompt_key in seen_prompts:
            return False
        if enforce_unique_focus and focus_key and focus_key in used_focuses:
            return False
        if enforce_unique_semantic and semantic_key and semantic_key in used_semantic_keys:
            return False
        if enforce_unique_source and source_key and source_key in used_source_keys:
            return False
        if any(_question_payloads_are_redundant(normalized, existing) for existing in prepared):
            return False
        seen_prompts.add(prompt_key)
        if focus_key:
            used_focuses.add(focus_key)
        if semantic_key:
            used_semantic_keys.add(semantic_key)
        if source_key:
            used_source_keys.add(source_key)
        prepared.append(normalized)
        return True

    seeded_by_focus: dict[str, tuple[int, int, str, str, str, str, dict[str, Any]]] = {}
    for row in normalized_rows:
        _, _, focus_key, _, _, _, _ = row
        if not focus_key or focus_key in seeded_by_focus:
            continue
        seeded_by_focus[focus_key] = row
    diversity_seed_target = min(question_count, max(2, math.ceil(question_count * 0.6)))
    for _, _, focus_key, semantic_key, source_key, prompt_key, normalized in seeded_by_focus.values():
        try_add(
            normalized,
            focus_key=focus_key,
            semantic_key=semantic_key,
            source_key=source_key,
            prompt_key=prompt_key,
            enforce_unique_focus=True,
            enforce_unique_semantic=True,
            enforce_unique_source=True,
        )
        if len(prepared) >= diversity_seed_target:
            break

    for enforce_unique_focus, enforce_unique_semantic, enforce_unique_source in (
        (True, True, True),
        (False, True, True),
        (False, False, True),
        (False, False, False),
    ):
        for _, _, focus_key, semantic_key, source_key, prompt_key, normalized in normalized_rows:
            if prompt_key in seen_prompts:
                continue
            try_add(
                normalized,
                focus_key=focus_key,
                semantic_key=semantic_key,
                source_key=source_key,
                prompt_key=prompt_key,
                enforce_unique_focus=enforce_unique_focus,
                enforce_unique_semantic=enforce_unique_semantic,
                enforce_unique_source=enforce_unique_source,
            )
            if len(prepared) >= question_count:
                return prepared
    return prepared


def _filter_questions_against_existing(
    questions: list[dict[str, Any]],
    *,
    existing_questions: list[ChapterReviewTaskQuestion],
    unit: ChapterReviewUnit,
    question_count: int,
) -> list[dict[str, Any]]:
    existing_payloads = [_task_question_payload(question) for question in existing_questions]
    prepared_candidates = _prepare_questions_for_storage(
        questions,
        unit=unit,
        question_count=max(question_count, len(questions)),
    )
    selected: list[dict[str, Any]] = []
    for item in prepared_candidates:
        if any(_question_payloads_are_redundant(item, existing) for existing in existing_payloads):
            continue
        if any(_question_payloads_are_redundant(item, existing) for existing in selected):
            continue
        selected.append(item)
        if len(selected) >= question_count:
            break
    return selected


def _question_batch_is_usable(questions: list[dict[str, Any]], *, question_count: int) -> bool:
    if len(questions) < question_count:
        return False
    question_slice = questions[:question_count]
    low_quality_count = sum(1 for item in question_slice if _is_low_quality_question_payload(item))
    focus_keys, semantic_keys, max_focus_frequency = _question_batch_diversity_stats(question_slice)
    required_focuses = 1 if question_count <= 4 else min(question_count, max(3, math.ceil(question_count * 0.5)))
    required_semantic_keys = min(question_count, max(required_focuses + 2, math.ceil(question_count * 0.8)))
    return (
        low_quality_count == 0
        and len(focus_keys) >= required_focuses
        and len(semantic_keys) >= required_semantic_keys
        and max_focus_frequency <= max(2, math.ceil(question_count * 0.25))
    )


def _question_batch_is_serviceable(questions: list[dict[str, Any]], *, question_count: int) -> bool:
    if len(questions) < question_count:
        return False
    question_slice = questions[:question_count]
    low_quality_count = sum(1 for item in question_slice if _is_low_quality_question_payload(item))
    focus_keys, semantic_keys, max_focus_frequency = _question_batch_diversity_stats(question_slice)
    required_focuses = 1 if question_count <= 4 else min(question_count, max(3, math.ceil(question_count * 0.4)))
    required_semantic_keys = min(question_count, max(required_focuses + 1, math.ceil(question_count * 0.7)))
    return (
        low_quality_count <= max(1, question_count // 4)
        and len(focus_keys) >= required_focuses
        and len(semantic_keys) >= required_semantic_keys
        and max_focus_frequency <= max(2, math.ceil(question_count * 0.3))
    )


def _prompt_from_blueprint(item: dict[str, Any], *, variant_index: int = 0) -> tuple[str, str]:
    focus = _normalize_review_concept_name(item.get("prompt_focus") or item.get("concept_name")) or "该知识点"
    supporting_text = str(item.get("supporting_text") or item.get("source_excerpt") or "")
    axis = _normalize_blueprint_question_axis(item.get("question_axis"))
    axis_candidates = _blueprint_axis_candidates(item)
    chosen_axis = axis_candidates[variant_index % len(axis_candidates)]
    template_round = variant_index // len(axis_candidates)
    variants = _build_prompt_variants(focus, supporting_text)
    axis_templates = _axis_templates_for_focus(focus)
    ordered_variants = []
    for prompt in axis_templates.get(chosen_axis, []):
        if prompt not in ordered_variants:
            ordered_variants.append(prompt)
    for prompt in variants:
        if prompt not in ordered_variants:
            ordered_variants.append(prompt)
    chosen = ordered_variants[template_round % len(ordered_variants)]
    chosen = chosen.rstrip("。；; ")
    if chosen and chosen[-1] not in {"？", "?"}:
        chosen += "？"
    return chosen, chosen_axis


def _blueprint_axis_candidates(item: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(item.get(field) or "")
        for field in ("question_axis", "prompt_focus", "concept_name", "source_excerpt", "supporting_text")
    )
    candidates: list[str] = []

    def _add(axis: str) -> None:
        normalized = _normalize_blueprint_question_axis(axis)
        if normalized not in candidates:
            candidates.append(normalized)

    _add(str(item.get("question_axis") or "definition"))
    if any(token in text for token in ("机制", "反馈", "调节", "过程", "环节", "通路", "激活", "抑制", "方向")):
        _add("mechanism")
    if any(token in text for token in ("区别", "比较", "异同", "关系", "调定点", "相同", "不同")):
        _add("comparison")
    if any(token in text for token in ("意义", "目的", "风险", "稳态", "临床", "有利", "不利", "作用")):
        _add("significance")
    if any(token in text for token in ("特点", "特征", "表现", "例子", "实例", "类型", "分类")):
        _add("features")
    _add("definition")
    return candidates[:4]


def _axis_templates_for_focus(focus: str) -> dict[str, list[str]]:
    relation_like = any(token in focus for token in ("关系", "区别", "影响", "作用", "意义", "目标", "双重", "联系"))
    comparison_like = ("与" in focus and len(focus) <= 24) or any(token in focus for token in ("区别", "异同", "比较"))
    significance_focus = focus.endswith("意义") or focus.endswith("作用")
    mechanism_focus = focus.endswith("机制")
    templates = {
        "definition": [
            f"请简述{focus}的定义及其核心要点。",
            f"请围绕{focus}归纳最容易失分的关键要点。",
        ],
        "mechanism": [
            f"{focus}的核心机制是什么？请结合原文说明关键环节。",
            f"请说明{focus}的调节方向、关键环节及最终结果。",
        ],
        "comparison": [
            f"请概括{focus}与相关概念的关键区别，并指出答题抓手。",
            f"请比较{focus}与相近概念的异同点，并说明判别依据。",
        ],
        "significance": [
            f"{focus}在该生理或病理过程中有什么意义？请结合原文作答。",
            f"请概括{focus}的主要意义，并说明为什么这是本节重点。",
        ],
        "features": [
            f"请概括{focus}的主要特点，并说明答题时应覆盖哪些要点。",
            f"请归纳{focus}的核心特征，并指出常见失分点。",
        ],
    }
    if relation_like:
        templates["mechanism"] = [
            f"请结合原文说明{focus}背后的关键逻辑链。",
            f"请说明理解{focus}时必须交代的关键环节。",
        ]
        templates["significance"] = [
            f"请结合原文分析{focus}的生理或病理意义。",
            f"请说明{focus}为什么是本节容易失分的重点。",
        ]
    if comparison_like:
        templates["comparison"] = [
            f"请比较{focus}，并指出最关键的判别依据。",
            f"请结合原文说明{focus}时应抓住哪些区别点。",
        ]
    if mechanism_focus:
        templates["mechanism"] = [
            f"请结合原文说明{focus}，并交代关键环节与最终结果。",
            f"请概括{focus}的主线逻辑，并指出最容易漏掉的环节。",
        ]
    if significance_focus:
        templates["significance"] = [
            f"请结合原文分析{focus}，并说明为什么这是本节重点。",
            f"请说明{focus}在本节中的关键价值和答题抓手。",
        ]
    if focus.endswith("特点") or focus.endswith("特征") or "典型实例" in focus or "常见生理过程" in focus:
        templates["features"] = [
            f"请归纳{focus}，并说明答题时应覆盖哪些要点。",
            f"请概括{focus}的核心内容，并指出常见失分点。",
        ]
    return templates


def _questions_from_concept_blueprint(
    blueprint: list[dict[str, Any]],
    *,
    question_count: int,
) -> list[dict[str, Any]]:
    if not blueprint:
        return []
    questions: list[dict[str, Any]] = []
    seen_prompt_keys: set[str] = set()
    cycle_index = 0
    max_attempts = max(question_count * 8, len(blueprint) * 4)
    while len(questions) < question_count and cycle_index < max_attempts:
        item = dict(blueprint[cycle_index % len(blueprint)])
        variant_index = cycle_index // max(len(blueprint), 1)
        prompt, chosen_axis = _prompt_from_blueprint(item, variant_index=variant_index)
        prompt_key = _normalize_match_key(prompt)
        cycle_index += 1
        if not prompt_key or prompt_key in seen_prompt_keys:
            continue
        seen_prompt_keys.add(prompt_key)
        questions.append(
            {
                "prompt": prompt,
                "concept_name": str(item.get("concept_name") or ""),
                "prompt_focus": str(item.get("prompt_focus") or item.get("concept_name") or ""),
                "reference_answer": str(item.get("reference_answer") or ""),
                "key_points": list(item.get("expected_key_points") or item.get("key_points") or []),
                "explanation": str(item.get("explanation_hint") or item.get("explanation") or ""),
                "source_excerpt": str(item.get("source_excerpt") or ""),
                "supporting_text": str(item.get("supporting_text") or ""),
                "question_axis": chosen_axis,
            }
        )
    return questions[:question_count]


def _build_question_seed_candidates(
    unit: ChapterReviewUnit,
    *,
    summary: str,
    question_count: int,
    chapter_title: str,
) -> list[dict[str, Any]]:
    material = _build_generation_material(unit, summary=summary, chapter_title=chapter_title or unit.unit_title)
    seed_candidates = _questions_from_concept_blueprint(
        list(material.get("concept_blueprint") or []),
        question_count=question_count,
    )
    if not seed_candidates:
        seed_candidates = _fallback_questions_from_text(
            unit,
            question_count=question_count,
            summary=summary,
            chapter_title=chapter_title or unit.unit_title,
        )
    return _prepare_questions_for_storage(
        seed_candidates,
        unit=unit,
        question_count=question_count,
    )


def _compact_question_plan(seed_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index, item in enumerate(seed_candidates, start=1):
        plan.append(
            {
                "position": index,
                "concept_name": str(item.get("concept_name") or item.get("prompt_focus") or ""),
                "prompt_focus": str(item.get("prompt_focus") or item.get("concept_name") or ""),
                "question_axis": _question_axis_from_payload(item),
                "prompt_outline": str(item.get("prompt") or ""),
                "source_excerpt": str(item.get("source_excerpt") or ""),
                "expected_key_points": list(item.get("key_points") or [])[:4],
                "reference_answer_outline": str(item.get("reference_answer") or ""),
            }
        )
    return plan


def _merge_question_payloads_with_plan(
    raw_items: list[dict[str, Any]],
    *,
    seed_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not seed_plan:
        return list(raw_items or [])

    merged_by_position: dict[int, dict[str, Any]] = {}
    used_positions: set[int] = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            position = int(item.get("position") or 0)
        except (TypeError, ValueError):
            position = 0
        if 1 <= position <= len(seed_plan):
            base = dict(seed_plan[position - 1])
            base.update({key: value for key, value in item.items() if value not in (None, "", [], {})})
            merged_by_position[position] = base
            used_positions.add(position)

    unassigned: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            position = int(item.get("position") or 0)
        except (TypeError, ValueError):
            position = 0
        if position <= 0 or position not in used_positions:
            unassigned.append(item)
    unassigned_iter = iter(unassigned)
    merged_items: list[dict[str, Any]] = []
    for position, seed_item in enumerate(seed_plan, start=1):
        base = dict(seed_item)
        payload = merged_by_position.get(position)
        if payload is None:
            try:
                payload = next(unassigned_iter)
            except StopIteration:
                payload = {}
        if payload:
            base.update({key: value for key, value in payload.items() if value not in (None, "", [], {})})
        base["position"] = position
        merged_items.append(base)
    return merged_items


def _fallback_questions_from_text(
    unit: ChapterReviewUnit,
    *,
    question_count: int,
    summary: str = "",
    chapter_title: str = "",
) -> list[dict[str, Any]]:
    material = _build_generation_material(unit, summary=summary, chapter_title=chapter_title or unit.unit_title)
    if material.get("concept_blueprint"):
        return _questions_from_concept_blueprint(
            list(material.get("concept_blueprint") or []),
            question_count=question_count,
        )
    source_text = material["source_text"]
    resolved_summary = material["summary"]
    paragraphs = _extract_segments(source_text)
    if not paragraphs:
        paragraphs = [source_text[:UNIT_MAX_CHARS]]

    question_bank: list[dict[str, Any]] = []
    used_concepts: set[str] = set()
    used_paragraph_indices: set[int] = set()
    chapter_focus = _chapter_focus_from_unit_title(chapter_title or unit.unit_title)
    focus_pool: list[str] = []
    for candidate in list(material["focuses"]) + _extract_focus_candidates(source_text, limit=max(question_count * 2, 8)):
        if candidate not in focus_pool:
            focus_pool.append(candidate)
    if chapter_focus and chapter_focus not in focus_pool:
        focus_pool.append(chapter_focus)

    for question_index in range(question_count):
        preferred_focus = ""
        for focus in focus_pool:
            if focus not in used_concepts:
                preferred_focus = focus
                break
        paragraph_index, paragraph = _pick_best_paragraph(paragraphs, preferred_focus or chapter_focus, used_indices=used_paragraph_indices)
        used_paragraph_indices.add(paragraph_index)
        concept = preferred_focus
        for term in _extract_focus_candidates(paragraph, limit=10):
            if term not in used_concepts:
                concept = term
                break
        if not concept:
            concept = chapter_focus
        if not concept:
            concept = _clean_concept_candidate(resolved_summary) or _chapter_focus_from_unit_title(unit.unit_title) or "该知识点"
        used_concepts.add(concept)

        support_context = " ".join(part for part in [resolved_summary, paragraph] if part).strip() or paragraph
        reference_answer = _build_reference_answer_from_segment(paragraph, concept)
        key_points = _build_key_points_from_segment(paragraph, concept)
        source_excerpt = _build_source_excerpt_from_segment(paragraph, concept)
        prompt_variants = _build_prompt_variants(concept, paragraph)
        prompt = prompt_variants[question_index % len(prompt_variants)]
        question_bank.append(
            {
                "prompt": prompt,
                "reference_answer": reference_answer,
                "key_points": key_points,
                "explanation": _build_explanation_from_segment(support_context, concept, key_points),
                "source_excerpt": source_excerpt,
            }
        )
    return question_bank


def _supplement_question_candidates(
    unit: ChapterReviewUnit,
    *,
    questions: list[dict[str, Any]],
    question_count: int,
    summary: str = "",
    chapter_title: str = "",
) -> list[dict[str, Any]]:
    candidate_count = max(question_count * 2, question_count + 8, 18)
    supplemental = _fallback_questions_from_text(
        unit,
        question_count=candidate_count,
        summary=summary,
        chapter_title=chapter_title or unit.unit_title,
    )
    return _prepare_questions_for_storage(
        list(questions) + supplemental,
        unit=unit,
        question_count=question_count,
    )


def _build_emergency_question_set(
    unit: ChapterReviewUnit,
    *,
    question_count: int,
    summary: str = "",
    chapter_title: str = "",
) -> list[dict[str, Any]]:
    prepared = _prepare_questions_for_storage(
        _fallback_questions_from_text(
            unit,
            question_count=max(question_count * 2, question_count + 8, 18),
            summary=summary,
            chapter_title=chapter_title or unit.unit_title,
        ),
        unit=unit,
        question_count=question_count,
    )
    if prepared:
        prepared = _supplement_question_candidates(
            unit,
            questions=prepared,
            question_count=question_count,
            summary=summary,
            chapter_title=chapter_title or unit.unit_title,
        )
    if not prepared:
        source_text = clean_review_content(unit.cleaned_text or unit.raw_text or unit.excerpt or unit.unit_title)
        paragraph = (_extract_segments(source_text) or [source_text or unit.unit_title])[0]
        focus = _pick_question_focus("", paragraph, source_text, chapter_title or unit.unit_title)
        key_points = _build_key_points_from_segment(paragraph, focus)
        prepared = [
            {
                "prompt": _choose_fallback_prompt(focus, paragraph),
                "reference_answer": _build_reference_answer_from_segment(paragraph, focus),
                "key_points": key_points[:4],
                "explanation": _build_explanation_from_segment(paragraph, focus, key_points),
                "source_excerpt": _build_source_excerpt_from_segment(paragraph, focus),
            }
        ]

    results: list[dict[str, Any]] = []
    extension_index = 0
    while len(results) < question_count and prepared:
        cloned = dict(prepared[len(results) % len(prepared)])
        if len(results) >= len(prepared):
            extension_index += 1
            base_prompt = str(cloned.get("prompt") or "").rstrip("？?")
            cloned["prompt"] = f"{base_prompt}（延展{extension_index}）？"
        results.append(cloned)
    return results[:question_count]


async def _ai_generate_questions(unit: ChapterReviewUnit, summary: str, *, question_count: int) -> list[dict[str, Any]]:
    material = _build_generation_material(unit, summary=summary, chapter_title=unit.unit_title)
    available_focuses = max(len(material.get("concept_blueprint") or []), len(material.get("focuses") or []), 1)
    distinct_focus_target = min(
        question_count,
        max(3 if question_count <= 5 else 4, min(available_focuses, math.ceil(question_count * 0.6))),
    )
    max_per_focus = 1 if distinct_focus_target >= max(question_count - 2, 1) else 2
    prompt = f"""你是医学考研辅导名师。请严格基于给定复习材料，生成 {question_count} 道高质量复习题。

【章节】{material["chapter_title"]}
【当前复习单元】{unit.unit_title}
【章节摘要】{material["summary"] or "无"}
【章节知识点候选】{", ".join(material.get("chapter_concepts") or []) or "无"}
【知识点命题蓝图】
{material.get("blueprint_text") or "无"}
【命题焦点候选】{material["focus_line"] or "无"}
【高价值原文摘录】{material["digest"] or "无"}
【复习材料】
{material["source_text"]}

要求：
1. 每道题都必须可以从复习材料直接回答，不要引入材料外知识。
2. 题目必须优先围绕“知识点命题蓝图”中的知识点设计，每题都要明确对应一个知识点，不要直接把口语化原文短句当成考点。
3. 题目以简答题为主，聚焦定义、机制、鉴别点、流程、因果关系。至少覆盖 {distinct_focus_target} 个不同知识点，同一知识点最多 {max_per_focus} 题，不要围绕同一句话或同一原文定位反复换壳出题。
4. 题目措辞要精炼专业，不要直接复制原文作为题干。
5. 参考答案必须简洁准确，长度控制在 60-180 字，用专业术语组织语言。
6. key_points 保留 2-4 个关键得分点，每个要点用一句话概括核心知识。
7. explanation 必须包含知识点讲解（80-200字），要求：
   - 先说明该题考查的核心概念或机制
   - 再解释为什么答案是这样的（因果逻辑/生理机制）
   - 如有易混淆点或常见考研陷阱，务必指出
   - 可补充临床联系或记忆技巧
8. source_excerpt 必须摘自原文关键句，方便回看定位。
"""

    schema = {
        "questions": [
            {
                "prompt": "题目（精炼专业的问题）",
                "reference_answer": "参考答案（60-180字，专业术语）",
                "key_points": ["得分要点1", "得分要点2"],
                "explanation": "知识点讲解（80-200字，包含核心概念、因果机制、易混淆点）",
                "source_excerpt": "原文关键句定位",
            }
        ]
    }

    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=min(3600, 320 * max(question_count, 1) + 500),
        temperature=0.25,
        timeout=110,
        use_heavy=True,
    )
    return list(result.get("questions") or [])[:question_count]


async def _ai_refine_questions(unit: ChapterReviewUnit, summary: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not questions:
        return []
    material = _build_generation_material(unit, summary=summary, chapter_title=unit.unit_title)
    available_focuses = max(len(material.get("concept_blueprint") or []), len(material.get("focuses") or []), 1)
    distinct_focus_target = min(
        len(questions),
        max(3 if len(questions) <= 5 else 4, min(available_focuses, math.ceil(len(questions) * 0.6))),
    )
    prompt = f"""你是医学考研命题编辑。请在不引入原文外知识的前提下，对下面这批复习题做一次精修。

【章节】{material["chapter_title"]}
【当前复习单元】{unit.unit_title}
【章节摘要】{material["summary"] or "无"}
【章节知识点候选】{", ".join(material.get("chapter_concepts") or []) or "无"}
【知识点命题蓝图】
{material.get("blueprint_text") or "无"}
【命题焦点候选】{material["focus_line"] or "无"}
【高价值原文摘录】{material["digest"] or "无"}
【复习材料】
{material["source_text"]}

【待精修题目】
{json.dumps(questions, ensure_ascii=False, indent=2)}

要求：
1. 保留原题考点，但把题干改写成完整、自然、专业的简答题，并确保题目真正对应某个明确知识点。
2. 禁止出现“那么/所以/对吧/这就/那你”等口语残句开头。
3. 禁止让题干围绕口语碎片、代词、课堂过渡语命题，优先改成围绕“知识点命题蓝图”的明确知识点。
4. 精修后整套题至少覆盖 {distinct_focus_target} 个不同知识点，尽量降低重复；如果两题考同一知识点，题干角度必须明显不同。
5. reference_answer 控制在 60-180 字，必须可直接由原文支持。
6. key_points 保留 2-4 条，每条都是可判分的要点，不要直接抄长句。
7. explanation 控制在 80-200 字，要解释考点、因果逻辑和易错点。
8. source_excerpt 需摘自原文关键句，方便回看定位。
9. 只返回 JSON。
"""
    schema = {
        "questions": [
            {
                "prompt": "精修后的题干",
                "reference_answer": "精修后的参考答案",
                "key_points": ["要点1", "要点2"],
                "explanation": "精修后的解析",
                "source_excerpt": "原文关键句",
            }
        ]
    }
    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=min(3000, 220 * len(questions) + 500),
        temperature=0.15,
        timeout=90,
        use_heavy=False,
    )
    return list(result.get("questions") or [])[: len(questions)]


async def _ai_rewrite_question_explanations(
    unit: ChapterReviewUnit,
    summary: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not questions:
        return []
    source_text = clean_review_content(unit.cleaned_text or unit.raw_text or unit.excerpt or unit.unit_title)
    paragraphs = _extract_segments(source_text) or [source_text]
    material = _build_generation_material(unit, summary=summary, chapter_title=unit.unit_title)
    rewrite_payload = [
        {
            "position": index,
            "prompt": str(item.get("prompt") or ""),
            "reference_answer": str(item.get("reference_answer") or ""),
            "key_points": list(item.get("key_points") or [])[:4],
            "source_excerpt": str(item.get("source_excerpt") or ""),
            "explanation": str(item.get("explanation") or ""),
        }
        for index, item in enumerate(questions, start=1)
    ]
    prompt = f"""你是医学考研主观题解析编辑。请只重写下面每道题的 explanation，其他字段都不要改，也不要补充原文之外的知识。

【章节】{unit.unit_title}
【章节摘要】{material["summary"] or "无"}
【章节知识点候选】{", ".join(material.get("chapter_concepts") or []) or "无"}
【知识点命题蓝图】
{material.get("blueprint_text") or "无"}
【高价值原文摘录】{material["digest"] or "无"}
【复习材料】
{material["source_text"]}

【待改写题目】
{json.dumps(rewrite_payload, ensure_ascii=False, indent=2)}

要求：
1. 每条 explanation 控制在 110-220 字，必须是书面化、可直接给学生看的医学考试解析。
2. 开头先点明本题真正考什么，不能只是重复题干。
3. 中间解释参考答案为什么要这样组织，必要时写清因果链、判别标准或作用定位。
4. 必须点出至少一个易错点、失分点或答题陷阱。
5. 结尾给出简短作答抓手，例如先写什么、再写什么。
6. 禁止空话模板，例如“请结合原文作答”“答案应覆盖原文要点”。
7. 禁止大段照抄 reference_answer 或 source_excerpt，必须在其基础上改写。
8. 只返回 JSON。
"""
    schema = {
        "questions": [
            {
                "position": 1,
                "explanation": "重写后的解析",
            }
        ]
    }
    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=min(3200, 180 * len(questions) + 400),
        temperature=0.15,
        timeout=45,
        use_heavy=False,
    )
    rewritten_by_position: dict[int, str] = {}
    for item in list(result.get("questions") or []):
        try:
            position = int(item.get("position") or 0)
        except (TypeError, ValueError):
            continue
        if position <= 0:
            continue
        explanation = _trim_text(_polish_source_sentence(str(item.get("explanation") or "")), max_length=220)
        if not explanation:
            continue
        rewritten_by_position[position] = explanation

    rewritten_questions: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        rewritten = dict(item)
        candidate_explanation = rewritten_by_position.get(index, str(rewritten.get("explanation") or ""))
        focus = _pick_question_focus(
            str(rewritten.get("prompt") or ""),
            str(rewritten.get("source_excerpt") or ""),
            source_text,
            unit.unit_title,
        )
        _, best_paragraph = _pick_best_paragraph(paragraphs, focus, used_indices=set())
        key_points = [
            _trim_text(_polish_source_sentence(point), max_length=28)
            for point in list(rewritten.get("key_points") or [])
            if _polish_source_sentence(point)
        ]
        key_points = [point for point in key_points if point and not _key_point_is_low_quality(point)]
        if len(key_points) < 2:
            key_points = _build_key_points_from_segment(best_paragraph, focus)
        if _explanation_is_low_quality(
            candidate_explanation,
            prompt=str(rewritten.get("prompt") or ""),
            reference_answer=str(rewritten.get("reference_answer") or ""),
            key_points=key_points,
            source_excerpt=str(rewritten.get("source_excerpt") or ""),
        ):
            candidate_explanation = _build_explanation_from_segment(best_paragraph, focus, key_points)
        rewritten["explanation"] = candidate_explanation
        rewritten_questions.append(rewritten)
    return rewritten_questions


async def _ai_generate_questions_v2(
    unit: ChapterReviewUnit,
    summary: str,
    *,
    question_count: int,
) -> list[dict[str, Any]]:
    material = _build_generation_material(unit, summary=summary, chapter_title=unit.unit_title)
    seed_candidates = _build_question_seed_candidates(
        unit,
        summary=summary,
        question_count=question_count,
        chapter_title=material["chapter_title"],
    )
    question_plan = _compact_question_plan(seed_candidates)
    prompt = f"""你是医学考研主观题命题编辑。请严格基于给定复习材料和命题计划，逐题生成 {len(question_plan)} 道高质量复习题。
【章节】{material["chapter_title"]}
【当前复习单元】{unit.unit_title}
【章节摘要】{material["summary"] or "无"}
【章节知识点候选】{", ".join(material.get("chapter_concepts") or []) or "无"}
【知识点命题蓝图】
{material.get("blueprint_text") or "无"}
【全章重点摘录】{material["digest"] or "无"}
【必须覆盖的命题计划】
{json.dumps(question_plan, ensure_ascii=False, indent=2)}

【复习材料】
{material["source_text"]}

要求：
1. 必须严格按命题计划逐题输出，questions 里的第 N 项必须对应计划里的 position=N，不能漏题、并题或改成同一知识点的重复题。
2. 每题都必须保留 concept_name、prompt_focus、question_axis，并与计划一致或更具体；不得围绕同一个 source_excerpt 反复换壳出题。
3. 题目必须书面化、专业化，优先考定义、机制、比较、意义、特点，不要直接照抄原文做题干。
4. reference_answer 控制在 70-190 字，key_points 保留 2-4 条可判分要点。
5. explanation 先给 90-170 字解析初稿，说明考点、因果逻辑和常见失分点，后续系统还会单独重写解析。
6. source_excerpt 必须引用最能支撑该题的原文关键句，便于回看定位。
7. 禁止引入材料外知识，禁止使用课堂口语、代词、过渡语作为考点。
8. 只返回 JSON。"""

    schema = {
        "questions": [
            {
                "position": 1,
                "concept_name": "知识点名称",
                "prompt_focus": "更具体的命题焦点",
                "question_axis": "mechanism",
                "prompt": "题目",
                "reference_answer": "参考答案",
                "key_points": ["得分点1", "得分点2"],
                "explanation": "解析初稿",
                "source_excerpt": "原文关键句",
            }
        ]
    }

    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=min(4800, 300 * max(len(question_plan), 1) + 900),
        temperature=0.25,
        timeout=150,
        use_heavy=True,
    )
    return _merge_question_payloads_with_plan(
        list(result.get("questions") or []),
        seed_plan=question_plan,
    )[:question_count]


async def _ai_refine_questions_v2(
    unit: ChapterReviewUnit,
    summary: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not questions:
        return []
    material = _build_generation_material(unit, summary=summary, chapter_title=unit.unit_title)
    prepared_candidates = _prepare_questions_for_storage(
        questions,
        unit=unit,
        question_count=len(questions),
    )
    question_plan = _compact_question_plan(prepared_candidates)
    candidate_payload = []
    for position, item in enumerate(prepared_candidates, start=1):
        candidate_payload.append(
            {
                "position": position,
                "concept_name": str(item.get("concept_name") or ""),
                "prompt_focus": str(item.get("prompt_focus") or ""),
                "question_axis": _question_axis_from_payload(item),
                "prompt": str(item.get("prompt") or ""),
                "reference_answer": str(item.get("reference_answer") or ""),
                "key_points": list(item.get("key_points") or [])[:4],
                "explanation": str(item.get("explanation") or ""),
                "source_excerpt": str(item.get("source_excerpt") or ""),
            }
        )

    prompt = f"""你是医学考研命题质检编辑。请在不引入材料外知识的前提下，对下面这批候选题做去重、筛选和精修。
【章节】{material["chapter_title"]}
【当前复习单元】{unit.unit_title}
【章节摘要】{material["summary"] or "无"}
【知识点命题蓝图】
{material.get("blueprint_text") or "无"}
【应优先保持的命题计划】
{json.dumps(question_plan, ensure_ascii=False, indent=2)}
【待精修候选题】
{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}

【复习材料】
{material["source_text"]}

要求：
1. 按原 position 返回精修结果，不要漏题，不要把多个 position 改成同一知识点的换壳题。
2. concept_name、prompt_focus、question_axis 必须保留，并与原候选一致或更具体。
3. 优先消除同一知识点、同一 axis、同一 source_excerpt 的重复题；题干要更书面化、更像真正的医学简答题。
4. reference_answer、key_points、source_excerpt 必须能被原文直接支撑。
5. explanation 只给解析初稿，重点解释考点、作答逻辑和常见失分点。
6. 只返回 JSON。"""

    schema = {
        "questions": [
            {
                "position": 1,
                "concept_name": "知识点名称",
                "prompt_focus": "命题焦点",
                "question_axis": "definition",
                "prompt": "精修后的题目",
                "reference_answer": "精修后的参考答案",
                "key_points": ["要点1", "要点2"],
                "explanation": "解析初稿",
                "source_excerpt": "原文关键句",
            }
        ]
    }
    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=min(3600, 210 * max(len(candidate_payload), 1) + 800),
        temperature=0.15,
        timeout=120,
        use_heavy=True,
    )
    return _merge_question_payloads_with_plan(
        list(result.get("questions") or []),
        seed_plan=question_plan,
    )[: len(questions)]


async def _ai_rewrite_question_explanations_v2(
    unit: ChapterReviewUnit,
    summary: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not questions:
        return []
    material = _build_generation_material(unit, summary=summary, chapter_title=unit.unit_title)
    rewrite_payload = []
    for index, item in enumerate(questions, start=1):
        rewrite_payload.append(
            {
                "position": index,
                "concept_name": str(item.get("concept_name") or item.get("prompt_focus") or ""),
                "prompt_focus": str(item.get("prompt_focus") or item.get("concept_name") or ""),
                "question_axis": _question_axis_from_payload(item),
                "prompt": str(item.get("prompt") or ""),
                "reference_answer": str(item.get("reference_answer") or ""),
                "key_points": list(item.get("key_points") or [])[:4],
                "source_excerpt": str(item.get("source_excerpt") or ""),
                "explanation": str(item.get("explanation") or ""),
            }
        )

    prompt = f"""你是医学考研主观题解析编辑。请只重写下面每道题的 explanation，其他字段不要改，也不要引入原文之外的知识。
【章节】{material["chapter_title"]}
【当前复习单元】{unit.unit_title}
【章节摘要】{material["summary"] or "无"}
【知识点命题蓝图】
{material.get("blueprint_text") or "无"}
【待重写题目】
{json.dumps(rewrite_payload, ensure_ascii=False, indent=2)}

【复习材料】
{material["source_text"]}

要求：
1. 每条 explanation 控制在 120-240 字，必须像真实的医学考试解析，而不是模板话。
2. 开头先点明本题真正考查的知识点或判断轴线。
3. 中间解释为什么参考答案要这样组织，尽量写清因果逻辑、机制链、判断标准或比较抓手。
4. 必须点出至少一个易错点、失分点或常见混淆点。
5. 结尾给一句可执行的作答抓手，例如“先写什么，再写什么”。
6. 禁止空话，禁止大段照抄 reference_answer 或 source_excerpt。
7. 只返回 JSON。"""
    schema = {
        "questions": [
            {
                "position": 1,
                "explanation": "重写后的解析",
            }
        ]
    }
    result = await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=min(3600, 170 * max(len(rewrite_payload), 1) + 600),
        temperature=0.15,
        timeout=80,
        use_heavy=True,
    )
    rewritten_by_position: dict[int, str] = {}
    for item in list(result.get("questions") or []):
        try:
            position = int(item.get("position") or 0)
        except (TypeError, ValueError):
            continue
        if position <= 0:
            continue
        explanation = _trim_text(_polish_source_sentence(str(item.get("explanation") or "")), max_length=240)
        if explanation:
            rewritten_by_position[position] = explanation

    rewritten_questions: list[dict[str, Any]] = []
    chapter_source_text = clean_review_content(material.get("chapter_source_text") or material.get("source_text") or "")
    paragraphs = _extract_segments(chapter_source_text or material.get("source_text") or "") or [chapter_source_text or material.get("source_text") or ""]
    for index, item in enumerate(questions, start=1):
        rewritten = dict(item)
        explanation = rewritten_by_position.get(index, str(rewritten.get("explanation") or ""))
        focus = _pick_question_focus(
            str(rewritten.get("prompt") or ""),
            str(rewritten.get("source_excerpt") or ""),
            chapter_source_text or material.get("source_text") or "",
            material["chapter_title"],
        )
        _, best_paragraph = _pick_best_paragraph(paragraphs, focus, used_indices=set())
        key_points = [
            _trim_text(_polish_source_sentence(point), max_length=28)
            for point in list(rewritten.get("key_points") or [])
            if _polish_source_sentence(point)
        ]
        key_points = [point for point in key_points if point and not _key_point_is_low_quality(point)]
        if len(key_points) < 2:
            key_points = _build_key_points_from_segment(best_paragraph, focus)
        if _explanation_is_low_quality(
            explanation,
            prompt=str(rewritten.get("prompt") or ""),
            reference_answer=str(rewritten.get("reference_answer") or ""),
            key_points=key_points,
            source_excerpt=str(rewritten.get("source_excerpt") or ""),
        ):
            explanation = _build_explanation_from_segment(best_paragraph, focus, key_points)
        rewritten["explanation"] = explanation
        rewritten_questions.append(rewritten)
    return rewritten_questions


_DEFAULT_AI_GENERATE_QUESTIONS = _ai_generate_questions
_DEFAULT_AI_REFINE_QUESTIONS = _ai_refine_questions
_DEFAULT_AI_REWRITE_QUESTION_EXPLANATIONS = _ai_rewrite_question_explanations


async def _generate_questions_pipeline(
    unit: ChapterReviewUnit,
    summary: str,
    *,
    question_count: int,
) -> list[dict[str, Any]]:
    if _ai_generate_questions is not _DEFAULT_AI_GENERATE_QUESTIONS:
        return await _ai_generate_questions(unit, summary, question_count=question_count)
    return await _ai_generate_questions_v2(unit, summary, question_count=question_count)


async def _refine_questions_pipeline(
    unit: ChapterReviewUnit,
    summary: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if _ai_refine_questions is not _DEFAULT_AI_REFINE_QUESTIONS:
        return await _ai_refine_questions(unit, summary, questions)
    return await _ai_refine_questions_v2(unit, summary, questions)


async def _rewrite_question_explanations_pipeline(
    unit: ChapterReviewUnit,
    summary: str,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if _ai_rewrite_question_explanations is not _DEFAULT_AI_REWRITE_QUESTION_EXPLANATIONS:
        return await _ai_rewrite_question_explanations(unit, summary, questions)
    return await _ai_rewrite_question_explanations_v2(unit, summary, questions)


def _is_low_quality_question(q: ChapterReviewTaskQuestion) -> bool:
    """
    检测低质量题目（旧版 fallback 生成的垃圾题）。
    判定条件（满足任一即为低质量）：
      1. 题干包含"请根据复习材料，概述以下内容的核心要点"（旧fallback模板）
      2. 解析是千篇一律的模板句（无实质内容）
      3. 参考答案和题干高度重复（直接复制原文当答案）
    """
    payload = {
        "prompt": q.prompt or "",
        "reference_answer": q.reference_answer or "",
        "key_points": list(q.key_points or []),
        "explanation": q.explanation or "",
        "source_excerpt": q.source_excerpt or "",
    }
    prompt = q.prompt or ""
    explanation = q.explanation or ""
    ref_answer = q.reference_answer or ""

    # 旧 fallback 模板特征
    if "请根据复习材料，概述以下内容的核心要点" in prompt:
        return True
    if "请概述以下内容的核心要点" in prompt:
        return True
    if _is_low_quality_question_payload(payload):
        return True

    # 解析是空洞模板
    _garbage_explanations = {
        "作答时尽量覆盖原文中的核心事实、概念关系和结论。",
        "请结合原文关键事实作答。",
    }
    if explanation.strip() in _garbage_explanations:
        return True
    if len(explanation.strip()) < 45:
        return True
    if len(ref_answer.strip()) < 40:
        return True
    if len(list(q.key_points or [])) < 2:
        return True
    if re.match(r"^(?:那么|所以|对吧|好|然后|接下来|最后|另外|这就|那你|你看)", prompt.strip()):
        return True

    # 参考答案和题干几乎一样（去掉模板前缀后比较）
    clean_prompt = prompt.replace("请根据复习材料，概述以下内容的核心要点：", "").strip()
    if clean_prompt and ref_answer.strip() == clean_prompt:
        return True
    if _sequence_ratio(clean_prompt or prompt, ref_answer) > 0.66:
        return True

    return False


def _task_question_payload(question: ChapterReviewTaskQuestion) -> dict[str, Any]:
    return {
        "prompt": question.prompt or "",
        "reference_answer": question.reference_answer or "",
        "key_points": list(question.key_points or []),
        "explanation": question.explanation or "",
        "source_excerpt": question.source_excerpt or "",
    }


def _redundant_unanswered_questions(questions: list[ChapterReviewTaskQuestion]) -> list[ChapterReviewTaskQuestion]:
    answered_payloads = [
        _task_question_payload(question)
        for question in questions
        if str(question.user_answer or "").strip()
    ]
    unanswered = [question for question in questions if not str(question.user_answer or "").strip()]
    ranked = sorted(
        unanswered,
        key=lambda question: (
            _question_payload_quality_score(_task_question_payload(question)),
            -int(question.position or 0),
        ),
        reverse=True,
    )
    kept_payloads = list(answered_payloads)
    redundant: list[ChapterReviewTaskQuestion] = []
    for question in ranked:
        payload = _task_question_payload(question)
        if any(_question_payloads_are_redundant(payload, existing) for existing in kept_payloads):
            redundant.append(question)
            continue
        kept_payloads.append(payload)
    return redundant


async def _ensure_task_questions_once(db: Session, *, actor_key: str, task_id: int) -> ChapterReviewTask:
    """
    确保复习任务有题目。完整流程：

    1. 加载任务 + 关联的章节/单元/已有题目
    2. 质量检测：如果已有题目全部是低质量（旧fallback），自动删除并重新生成
    3. 生成题目：优先调用 AI → 失败则走改进版 fallback
    4. 持久化：写入 chapter_review_task_questions 表，标记 generation_source

    数据库关系：
        ChapterReviewTask (chapter_review_tasks)
          ├── review_chapter → ChapterReviewChapter (chapter_review_chapters)
          ├── unit → ChapterReviewUnit (chapter_review_units)
          └── questions → [ChapterReviewTaskQuestion] (chapter_review_task_questions)
    """
    task = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")

    question_count = int(task.question_count or QUESTIONS_PER_REVIEW_UNIT)

    # ── 质量检测：如果已有题目但全是低质量，删掉重新生成 ──
    if task.questions:
        low_quality_count = sum(1 for q in task.questions if _is_low_quality_question(q))
        redundant_questions = _redundant_unanswered_questions(list(task.questions))
        if low_quality_count == 0 and not redundant_questions and len(task.questions) >= question_count:
            return task  # 题目质量和数量都满足，直接返回
        # 有低质量或重复题目 → 只删除未作答的问题（保护用户已作答的数据）
        if low_quality_count > 0 or redundant_questions:
            to_delete = list(redundant_questions)
            to_delete.extend(
                q
                for q in task.questions
                if _is_low_quality_question(q) and not (q.user_answer or "").strip()
            )
            deduped_to_delete: list[ChapterReviewTaskQuestion] = []
            seen_ids: set[int] = set()
            for question in to_delete:
                question_id = int(question.id or 0)
                if question_id and question_id in seen_ids:
                    continue
                if question_id:
                    seen_ids.add(question_id)
                deduped_to_delete.append(question)
            to_delete = deduped_to_delete
            if not to_delete and len(task.questions) >= question_count:
                return task  # 用户已经答过了，而且数量已足够，不动
            for q in to_delete:
                db.delete(q)
            if to_delete:
                db.flush()
                # 刷新 questions 列表，确保后续补题按最新缺口计算
                db.refresh(task)

    # ── 计算需要生成的题目数量 ──
    existing_count = len(task.questions)
    need_count = question_count - existing_count
    if need_count <= 0:
        return task

    # ── 生成题目：AI 主生成 -> AI 精修；不足时混合 fallback 补位，再尝试精修 ──
    cleaned_unit_text = clean_review_content(task.unit.cleaned_text or task.unit.raw_text or task.unit.excerpt or "")
    summary = _resolve_review_summary(
        task.review_chapter.ai_summary or "",
        source_text=cleaned_unit_text,
        chapter_title=task.review_chapter.chapter_title,
    )
    generation_context = await _build_review_generation_context(
        db,
        task=task,
        source_text=cleaned_unit_text,
        summary=summary,
    )
    context_token = _review_generation_context.set(generation_context)
    candidate_count = need_count + max(4, math.ceil(need_count * 0.6))
    allow_sparse_question_set = len(cleaned_unit_text) < 120
    prefer_local_generation = (
        not str(task.review_chapter.ai_summary or "").strip()
        or _spoken_noise_score(cleaned_unit_text[:900]) >= 4
    )
    try:
        generation_source = "fallback" if prefer_local_generation else "ai"
        existing_questions_snapshot = list(task.questions)
        ai_candidates: list[dict[str, Any]] = []
        if not prefer_local_generation:
            try:
                ai_candidates = _prepare_questions_for_storage(
                    await asyncio.wait_for(
                        _generate_questions_pipeline(task.unit, summary, question_count=candidate_count),
                        timeout=22,
                    ),
                    unit=task.unit,
                    question_count=candidate_count,
                )
                try:
                    refined = _prepare_questions_for_storage(
                        await asyncio.wait_for(
                            _refine_questions_pipeline(task.unit, summary, ai_candidates),
                            timeout=10,
                        ),
                        unit=task.unit,
                        question_count=candidate_count,
                    )
                    if len(refined) > len(ai_candidates):
                        ai_candidates = refined
                    elif refined and _question_batch_is_usable(refined, question_count=min(need_count, len(refined))):
                        ai_candidates = refined
                except Exception:
                    pass
            except Exception:
                ai_candidates = []

        generated = ai_candidates[:need_count]
        if not _question_batch_is_usable(generated, question_count=need_count):
            generation_source = "fallback" if not ai_candidates else "ai+fallback"
            fallback_candidates = _prepare_questions_for_storage(
                _fallback_questions_from_text(
                    task.unit,
                    question_count=candidate_count,
                    summary=summary,
                    chapter_title=task.review_chapter.chapter_title,
                ),
                unit=task.unit,
                question_count=candidate_count,
            )
            combined_candidates = _prepare_questions_for_storage(
                ai_candidates + fallback_candidates,
                unit=task.unit,
                question_count=candidate_count,
            )
            try:
                refined_combined = _prepare_questions_for_storage(
                    await asyncio.wait_for(
                        _refine_questions_pipeline(task.unit, summary, combined_candidates),
                        timeout=10,
                    ),
                    unit=task.unit,
                    question_count=candidate_count,
                )
                if len(refined_combined) >= len(combined_candidates):
                    combined_candidates = refined_combined
                elif refined_combined and _question_batch_is_usable(refined_combined, question_count=min(need_count, len(refined_combined))):
                    combined_candidates = refined_combined
            except Exception:
                pass
            generated = combined_candidates[:need_count]

        if not _question_batch_is_usable(generated, question_count=need_count):
            if _question_batch_is_serviceable(generated, question_count=need_count):
                generated = generated[:need_count]
            else:
                generation_source = "emergency"
                generated = _build_emergency_question_set(
                    task.unit,
                    question_count=need_count,
                    summary=summary,
                    chapter_title=task.review_chapter.chapter_title,
                )

        if not generated or len(generated) < need_count:
            generation_source = "emergency"
            generated = _build_emergency_question_set(
                task.unit,
                question_count=need_count,
                summary=summary,
                chapter_title=task.review_chapter.chapter_title,
            )

        prepared_generated = _prepare_questions_for_storage(
            generated,
            unit=task.unit,
            question_count=need_count,
        )
        prepared_generated = _filter_questions_against_existing(
            prepared_generated,
            existing_questions=existing_questions_snapshot,
            unit=task.unit,
            question_count=need_count,
        )
        prepared_generated = _supplement_question_candidates(
            task.unit,
            questions=prepared_generated,
            question_count=max(need_count * 2, need_count + len(existing_questions_snapshot)),
            summary=summary,
            chapter_title=task.review_chapter.chapter_title,
        )
        prepared_generated = _filter_questions_against_existing(
            prepared_generated,
            existing_questions=existing_questions_snapshot,
            unit=task.unit,
            question_count=need_count,
        )
        try:
            rewritten_generated = _prepare_questions_for_storage(
                await asyncio.wait_for(
                    _rewrite_question_explanations_pipeline(task.unit, summary, prepared_generated),
                    timeout=18,
                ),
                unit=task.unit,
                question_count=need_count,
            )
            rewritten_generated = _supplement_question_candidates(
                task.unit,
                questions=rewritten_generated,
                question_count=need_count,
                summary=summary,
                    chapter_title=task.review_chapter.chapter_title,
                )
            rewritten_generated = _filter_questions_against_existing(
                rewritten_generated,
                existing_questions=existing_questions_snapshot,
                unit=task.unit,
                question_count=need_count,
            )
            if len(rewritten_generated) >= len(prepared_generated):
                prepared_generated = rewritten_generated
            elif rewritten_generated and _question_batch_is_serviceable(
                rewritten_generated,
                question_count=min(need_count, len(rewritten_generated)),
            ):
                prepared_generated = rewritten_generated
        except Exception:
            pass
        if len(prepared_generated) < need_count and allow_sparse_question_set:
            generated = _build_emergency_question_set(
                task.unit,
                question_count=need_count,
                summary=summary,
                chapter_title=task.review_chapter.chapter_title,
            )
        else:
            generated = prepared_generated[:need_count]
    finally:
        _review_generation_context.reset(context_token)
    if not generated or len(generated) < need_count:
        raise HTTPException(status_code=500, detail="复习题生成失败，请稍后重试")

    # ── 持久化到数据库 ──
    existing_positions = {int(question.position) for question in task.questions}
    target_positions = [
        position
        for position in range(1, question_count + 1)
        if position not in existing_positions
    ][:need_count]
    for index, item in zip(target_positions, generated[:need_count]):
        task.questions.append(
            ChapterReviewTaskQuestion(
                position=index,
                prompt=str(item.get("prompt") or f"请概述 {task.review_chapter.chapter_title} 的关键要点").strip(),
                reference_answer=str(item.get("reference_answer") or "").strip() or (task.unit.excerpt or task.unit.cleaned_text[:120]),
                key_points=list(item.get("key_points") or []),
                explanation=str(item.get("explanation") or "").strip() or "请结合原文关键事实作答。",
                source_excerpt=str(item.get("source_excerpt") or "").strip() or (task.unit.excerpt or task.unit.cleaned_text[:120]),
                generation_source=generation_source,
            )
        )

    db.flush()
    return task


async def ensure_task_questions(db: Session, *, actor_key: str, task_id: int) -> ChapterReviewTask:
    lock = _get_task_question_lock(actor_key, task_id)
    async with lock:
        for attempt in range(1, _SQLITE_LOCK_RETRY_ATTEMPTS + 1):
            try:
                return await _ensure_task_questions_once(db, actor_key=actor_key, task_id=task_id)
            except OperationalError as exc:
                db.rollback()
                if not _is_retryable_sqlite_lock_error(exc):
                    raise

                if attempt >= _SQLITE_LOCK_RETRY_ATTEMPTS:
                    task = (
                        db.query(ChapterReviewTask)
                        .options(
                            joinedload(ChapterReviewTask.review_chapter),
                            joinedload(ChapterReviewTask.unit),
                            joinedload(ChapterReviewTask.questions),
                        )
                        .filter(
                            ChapterReviewTask.id == task_id,
                            ChapterReviewTask.actor_key == actor_key,
                        )
                        .first()
                    )
                    if task is not None and task.questions:
                        logger.warning(
                            "sqlite lock while regenerating review task %s; returning existing %s questions",
                            task_id,
                            len(task.questions),
                        )
                        return task
                    raise HTTPException(status_code=503, detail="复习题正在生成，请稍后重试") from exc

                await asyncio.sleep(0.1 * attempt)


async def regenerate_task_questions(db: Session, *, actor_key: str, task_id: int) -> ChapterReviewTask:
    """
    强制重新生成任务题目。
    - 删除所有未作答的题目
    - 保留已作答的题目（保护用户数据）
    - 重新调用 AI 生成新题目补齐
    """
    task = (
        db.query(ChapterReviewTask)
        .options(
            joinedload(ChapterReviewTask.review_chapter),
            joinedload(ChapterReviewTask.unit),
            joinedload(ChapterReviewTask.questions),
        )
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")

    # 删除未作答的题目
    to_delete = [q for q in task.questions if not (q.user_answer or "").strip()]
    for q in to_delete:
        db.delete(q)
    db.flush()
    db.refresh(task)

    # 用 ensure_task_questions 补齐（此时已有题目被清空，会触发重新生成）
    return await ensure_task_questions(db, actor_key=actor_key, task_id=task_id)


def save_task_progress(
    db: Session,
    *,
    actor_key: str,
    task_id: int,
    answers: List[Dict[str, Any]],
    resume_position: int,
) -> Dict[str, Any]:
    task = (
        db.query(ChapterReviewTask)
        .options(joinedload(ChapterReviewTask.questions), joinedload(ChapterReviewTask.review_chapter), joinedload(ChapterReviewTask.unit))
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if task.status == "cancelled":
        raise HTTPException(status_code=409, detail="该任务已因章节更新而失效")

    question_map = {int(question.id): question for question in task.questions}
    position_map = {int(question.position): question for question in task.questions}
    for item in answers:
        question = None
        if item.get("question_id") is not None:
            question = question_map.get(int(item["question_id"]))
        if question is None and item.get("position") is not None:
            question = position_map.get(int(item["position"]))
        if question is None:
            continue
        question.user_answer = str(item.get("user_answer") or "").strip()
        question.updated_at = datetime.now()

    task.resume_position = max(int(resume_position or 0), 0)
    task.answered_count = sum(1 for question in task.questions if str(question.user_answer or "").strip())
    task.status = "in_progress" if task.answered_count else "pending"
    if task.started_at is None and task.answered_count:
        task.started_at = datetime.now()
    task.updated_at = datetime.now()
    task.unit.review_status = "in_progress" if task.answered_count else task.unit.review_status
    db.flush()
    return serialize_task_detail(task)


def _key_point_coverage_score(question: ChapterReviewTaskQuestion) -> tuple[int, list[str], list[str]]:
    answer = str(question.user_answer or "").strip()
    key_points = [str(item or "").strip() for item in list(question.key_points or []) if str(item or "").strip()]
    if not answer:
        return 0, [], key_points
    if not key_points:
        if len(answer) >= max(8, len(question.reference_answer or "") // 4):
            return 70, [answer[:24]], []
        return 30, [], []

    matched: list[str] = []
    missing: list[str] = []
    for point in key_points:
        if point and point in answer:
            matched.append(point)
        else:
            missing.append(point)
    score = int(round(len(matched) / max(len(key_points), 1) * 100))
    return score, matched, missing


async def _ai_grade_questions(task: ChapterReviewTask) -> Dict[str, Any]:
    question_payload = []
    for question in sorted(task.questions, key=lambda item: item.position):
        question_payload.append(
            {
                "position": int(question.position),
                "prompt": question.prompt,
                "reference_answer": question.reference_answer,
                "key_points": list(question.key_points or []),
                "source_excerpt": question.source_excerpt or "",
                "user_answer": question.user_answer or "",
            }
        )

    prompt = f"""你是医学简答题批改老师。请严格根据给定参考答案和原文要点，对学生回答进行逐题打分，并给出整体建议。

【章节】{task.review_chapter.chapter_title}
【单元】{task.unit.unit_title}
【题目列表】
{json.dumps(question_payload, ensure_ascii=False, indent=2)}

要求：
1. score 取 0-100。
2. good_points / missing_points 都尽量引用参考答案中的要点。
3. feedback 用一句话指出当前题的判断。
4. suggestion 给一句可执行建议。
5. recommended_status 只能是 weak / normal / mastered。
"""

    schema = {
        "results": [
            {
                "position": 1,
                "score": 80,
                "good_points": ["答到了什么"],
                "missing_points": ["漏掉了什么"],
                "feedback": "这一题的判断",
                "suggestion": "改进建议",
            }
        ],
        "recommended_status": "normal",
        "overall_feedback": "整体建议",
    }

    return await get_ai_client().generate_json(
        prompt,
        schema,
        max_tokens=4200,
        temperature=0.15,
        timeout=150,
        use_heavy=False,
    )


async def grade_task_answers(db: Session, *, actor_key: str, task_id: int) -> Dict[str, Any]:
    task = (
        db.query(ChapterReviewTask)
        .options(joinedload(ChapterReviewTask.questions), joinedload(ChapterReviewTask.review_chapter), joinedload(ChapterReviewTask.unit))
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if not task.questions:
        raise HTTPException(status_code=400, detail="请先生成复习题目")

    missing_positions = [
        int(question.position)
        for question in task.questions
        if not str(question.user_answer or "").strip()
    ]
    if missing_positions:
        raise HTTPException(status_code=400, detail=f"还有 {len(missing_positions)} 道题未作答，不能完成本轮复习")

    try:
        grading = await _ai_grade_questions(task)
        ai_results = {int(item.get("position")): item for item in list(grading.get("results") or [])}
        recommended_status = str(grading.get("recommended_status") or "normal").strip().lower()
        overall_feedback = str(grading.get("overall_feedback") or "").strip()
    except Exception:
        ai_results = {}
        overall_feedback = "AI 批改不可用，已切换到本地匹配规则。"
        recommended_status = "normal"

    total_score = 0
    for question in task.questions:
        payload = ai_results.get(int(question.position))
        if payload is None:
            score, matched, missing = _key_point_coverage_score(question)
            feedback = "回答覆盖了较多原文要点。" if score >= 70 else "回答还不够完整，建议回看原文定位片段。"
            suggestion = "优先补全漏掉的关键点，再重新组织成完整表述。"
            good_points = matched
            missing_points = missing
        else:
            score = int(payload.get("score") or 0)
            feedback = str(payload.get("feedback") or "").strip()
            suggestion = str(payload.get("suggestion") or "").strip()
            good_points = [str(item).strip() for item in list(payload.get("good_points") or []) if str(item).strip()]
            missing_points = [str(item).strip() for item in list(payload.get("missing_points") or []) if str(item).strip()]

        question.ai_score = max(0, min(score, 100))
        question.ai_feedback = feedback
        question.good_points = good_points
        question.missing_points = missing_points
        question.improvement_suggestion = suggestion
        question.judged_at = datetime.now()
        total_score += int(question.ai_score or 0)

    average_score = round(total_score / max(len(task.questions), 1), 1)
    if recommended_status not in {"weak", "normal", "mastered"}:
        if average_score >= 85:
            recommended_status = "mastered"
        elif average_score >= 60:
            recommended_status = "normal"
        else:
            recommended_status = "weak"

    task.grading_score = average_score
    task.ai_recommended_status = recommended_status
    task.graded_at = datetime.now()
    task.status = "awaiting_choice"
    task.updated_at = datetime.now()
    db.flush()

    payload = serialize_task_detail(task)
    payload["overall_feedback"] = overall_feedback
    payload["ai_recommended_status"] = recommended_status
    payload["grading_score"] = average_score
    return payload


def complete_task_with_status(
    db: Session,
    *,
    actor_key: str,
    task_id: int,
    selected_status: str,
) -> Dict[str, Any]:
    normalized_status = str(selected_status or "").strip().lower()
    if normalized_status not in {"weak", "normal", "mastered"}:
        raise HTTPException(status_code=400, detail="复习状态只能是 weak / normal / mastered")

    task = (
        db.query(ChapterReviewTask)
        .options(joinedload(ChapterReviewTask.questions), joinedload(ChapterReviewTask.review_chapter), joinedload(ChapterReviewTask.unit))
        .filter(
            ChapterReviewTask.id == task_id,
            ChapterReviewTask.actor_key == actor_key,
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    if task.status == "cancelled":
        raise HTTPException(status_code=409, detail="该任务已因章节更新而失效")
    if any(not str(question.user_answer or "").strip() for question in task.questions):
        raise HTTPException(status_code=400, detail="还有未完成的题目，不能结束本轮复习")

    today = date.today()
    unit = task.unit
    current_round = int(unit.next_round or 1)
    unit.last_reviewed_at = today
    unit.last_status_label = normalized_status
    unit.carry_over_count = 0

    if normalized_status == "weak":
        unit.review_status = "weak"
        unit.next_due_date = today + timedelta(days=1)
    else:
        unit.completed_rounds = max(int(unit.completed_rounds or 0), current_round)
        if current_round >= len(REVIEW_INTERVAL_DAYS):
            unit.review_status = "completed"
            unit.next_due_date = None
        else:
            next_round = current_round + 1
            unit.next_round = next_round
            unit.next_due_date = today + timedelta(days=REVIEW_INTERVAL_DAYS[next_round - 1])
            unit.review_status = "pending" if normalized_status == "normal" else "mastered"

    task.user_selected_status = normalized_status
    task.status = "completed"
    task.completed_at = datetime.now()
    task.updated_at = datetime.now()
    task.resume_position = 0
    task.answered_count = sum(1 for question in task.questions if str(question.user_answer or "").strip())

    task.review_chapter.last_reviewed_at = today
    _recompute_chapter_due(task.review_chapter)
    db.flush()
    return serialize_task_detail(task)


def _serialize_pdf_task_block(task: ChapterReviewTask) -> Dict[str, Any]:
    source_text = _task_chapter_source_text(task)
    return {
        "chapter_title": task.review_chapter.chapter_title,
        "book": task.review_chapter.book,
        "unit_title": task.unit.unit_title,
        "due_reason": task.due_reason,
        "summary": _resolve_review_summary(
            task.review_chapter.ai_summary or "",
            source_text=source_text,
            chapter_title=task.review_chapter.chapter_title,
        ),
        "excerpt": task.unit.excerpt or "",
        "source_content": source_text,
        "questions": [
            {
                "position": int(question.position),
                "prompt": question.prompt,
                "reference_answer": question.reference_answer,
                "key_points": list(question.key_points or []),
                "explanation": question.explanation or "",
                "source_excerpt": question.source_excerpt or "",
            }
            for question in sorted(task.questions, key=lambda item: item.position)
        ],
    }


def _paragraphize_pdf_text(value: Optional[str]) -> str:
    text = str(value or "").strip() or "暂无内容"
    return xml_escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def build_review_pdf(
    *,
    review_date: date,
    tasks: List[ChapterReviewTask],
    time_budget_minutes: int,
) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate,
            Frame,
            HRFlowable,
            KeepTogether,
            NextPageTemplate,
            PageBreak,
            PageTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 依赖不可用: {exc}") from exc

    from routers.wrong_answers_v2 import _get_embedded_pdf_font_name

    font_name = _get_embedded_pdf_font_name()
    sample_styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=18 * mm,
        bottomMargin=12 * mm,
        title=f"章节复习题单 {review_date.isoformat()}",
        author="True Learning System",
    )
    page_width, page_height = A4
    usable_width = page_width - doc.leftMargin - doc.rightMargin
    usable_height = page_height - doc.topMargin - doc.bottomMargin
    column_gap = 6 * mm
    question_column_width = (usable_width - column_gap) / 2
    first_page_summary_height = 40 * mm
    first_page_gap = 4 * mm
    first_page_question_height = usable_height - first_page_summary_height - first_page_gap

    question_frames = [
        Frame(doc.leftMargin, doc.bottomMargin, question_column_width, usable_height, id="question-col-1", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(doc.leftMargin + question_column_width + column_gap, doc.bottomMargin, question_column_width, usable_height, id="question-col-2", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
    ]
    first_page_frames = [
        Frame(doc.leftMargin, doc.bottomMargin + first_page_question_height + first_page_gap, usable_width, first_page_summary_height, id="first-summary", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(doc.leftMargin, doc.bottomMargin, question_column_width, first_page_question_height, id="first-question-col-1", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(doc.leftMargin + question_column_width + column_gap, doc.bottomMargin, question_column_width, first_page_question_height, id="first-question-col-2", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
    ]
    appendix_frames = [
        Frame(doc.leftMargin, doc.bottomMargin, usable_width, usable_height, id="appendix-col", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
    ]

    def draw_question_page(canvas, current_doc):
        canvas.saveState()
        canvas.setFont(font_name, 9)
        canvas.setFillColor(colors.HexColor("#5B6472"))
        canvas.drawString(current_doc.leftMargin, page_height - 9 * mm, "历史复习 · 打印题单")
        canvas.drawRightString(page_width - current_doc.rightMargin, page_height - 9 * mm, review_date.strftime("%Y-%m-%d"))
        canvas.setStrokeColor(colors.HexColor("#D0D7DE"))
        canvas.line(current_doc.leftMargin, page_height - 10.8 * mm, page_width - current_doc.rightMargin, page_height - 10.8 * mm)
        canvas.drawRightString(page_width - current_doc.rightMargin, 7 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    def draw_appendix_page(canvas, current_doc):
        canvas.saveState()
        canvas.setFont(font_name, 9)
        canvas.setFillColor(colors.HexColor("#5B6472"))
        canvas.drawString(current_doc.leftMargin, page_height - 9 * mm, "历史复习 · 答案解析附页")
        canvas.drawRightString(page_width - current_doc.rightMargin, page_height - 9 * mm, review_date.strftime("%Y-%m-%d"))
        canvas.setStrokeColor(colors.HexColor("#D0D7DE"))
        canvas.line(current_doc.leftMargin, page_height - 10.8 * mm, page_width - current_doc.rightMargin, page_height - 10.8 * mm)
        canvas.drawRightString(page_width - current_doc.rightMargin, 7 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="first-questions", frames=first_page_frames, onPage=draw_question_page, autoNextPageTemplate="questions"),
        PageTemplate(id="questions", frames=question_frames, onPage=draw_question_page),
        PageTemplate(id="appendix", frames=appendix_frames, onPage=draw_appendix_page),
    ])

    title_style = ParagraphStyle(
        "HistoryReviewTitle",
        parent=sample_styles["Title"],
        fontName=font_name,
        fontSize=16.5,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#111827"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "HistoryReviewSubtitle",
        parent=sample_styles["Normal"],
        fontName=font_name,
        fontSize=8.8,
        leading=11.4,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#5B6472"),
        wordWrap="CJK",
        spaceAfter=4,
    )
    summary_line_style = ParagraphStyle(
        "HistoryReviewSummaryLine",
        parent=sample_styles["Normal"],
        fontName=font_name,
        fontSize=8.8,
        leading=11.2,
        textColor=colors.HexColor("#334155"),
        wordWrap="CJK",
        spaceAfter=0,
    )
    task_header_style = ParagraphStyle(
        "HistoryReviewTaskHeader",
        parent=sample_styles["Heading2"],
        fontName=font_name,
        fontSize=9.4,
        leading=12.2,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=2,
        spaceAfter=2,
    )
    task_meta_style = ParagraphStyle(
        "HistoryReviewTaskMeta",
        parent=sample_styles["Normal"],
        fontName=font_name,
        fontSize=8.2,
        leading=10.8,
        textColor=colors.HexColor("#475569"),
        wordWrap="CJK",
        spaceAfter=2,
    )
    question_meta_style = ParagraphStyle(
        "HistoryReviewQuestionMeta",
        parent=sample_styles["Normal"],
        fontName=font_name,
        fontSize=8.2,
        leading=10.6,
        textColor=colors.HexColor("#3B4A5A"),
        spaceAfter=1,
    )
    question_text_style = ParagraphStyle(
        "HistoryReviewQuestionText",
        parent=sample_styles["BodyText"],
        fontName=font_name,
        fontSize=9.6,
        leading=13.2,
        textColor=colors.HexColor("#0F172A"),
        wordWrap="CJK",
        spaceAfter=2,
    )
    prompt_hint_style = ParagraphStyle(
        "HistoryReviewPromptHint",
        parent=sample_styles["Normal"],
        fontName=font_name,
        fontSize=7.8,
        leading=10.2,
        textColor=colors.HexColor("#64748B"),
        wordWrap="CJK",
        spaceAfter=2,
    )
    appendix_title_style = ParagraphStyle(
        "HistoryReviewAppendixTitle",
        parent=sample_styles["Heading1"],
        fontName=font_name,
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#111827"),
        spaceAfter=6,
    )
    appendix_task_style = ParagraphStyle(
        "HistoryReviewAppendixTask",
        parent=sample_styles["Heading2"],
        fontName=font_name,
        fontSize=10.4,
        leading=13.6,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=4,
        spaceAfter=3,
    )
    appendix_item_style = ParagraphStyle(
        "HistoryReviewAppendixItem",
        parent=sample_styles["BodyText"],
        fontName=font_name,
        fontSize=9.2,
        leading=12.8,
        textColor=colors.HexColor("#1F2937"),
        wordWrap="CJK",
        spaceAfter=3,
    )
    appendix_answer_style = ParagraphStyle(
        "HistoryReviewAppendixAnswer",
        parent=appendix_item_style,
        textColor=colors.HexColor("#0F766E"),
        spaceAfter=4,
    )
    appendix_keypoint_style = ParagraphStyle(
        "HistoryReviewAppendixKeyPoints",
        parent=appendix_item_style,
        textColor=colors.HexColor("#1D4ED8"),
        spaceAfter=3,
    )
    appendix_source_style = ParagraphStyle(
        "HistoryReviewAppendixSource",
        parent=appendix_item_style,
        textColor=colors.HexColor("#64748B"),
        fontSize=8.5,
        leading=11.4,
        spaceAfter=4,
    )

    blocks = [_serialize_pdf_task_block(task) for task in tasks]
    total_questions = sum(len(block["questions"]) for block in blocks)
    books = "、".join(sorted({block["book"] for block in blocks if block.get("book")})) or "未标注"
    chapter_titles = " / ".join([block["chapter_title"] for block in blocks[:4]])
    if len(blocks) > 4:
        chapter_titles += " / …"

    summary_table = Table(
        [[Paragraph(line, summary_line_style)] for line in [
            f"日期：{review_date.strftime('%Y-%m-%d')}    任务：{len(blocks)} 个    题量：{total_questions} 题    预计时长：{time_budget_minutes} 分钟",
            f"学科：{books}    章节：{chapter_titles or '未命名章节'}",
            "打印建议：先做题单，再对照附页中的参考答案、得分点、解析、原文定位与整章原文附录进行复盘。",
        ]],
        colWidths=[usable_width],
    )
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8.8),
        ("LEADING", (0, 0), (-1, -1), 11.2),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#334155")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    story: list[Any] = [
        Paragraph("章节复习题单", title_style),
        Paragraph("打印版短答复习稿。前半部分用于作答，后半部分集中提供参考答案、得分点、解析和原文定位。", subtitle_style),
        summary_table,
        Spacer(1, 2.5 * mm),
    ]

    def build_answer_box(width: float, line_count: int = 4) -> Table:
        box = Table([[""] for _ in range(line_count)], colWidths=[width], rowHeights=[6 * mm] * line_count)
        box.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return box

    for task_index, block in enumerate(blocks, start=1):
        task_flowables: list[Any] = [
            Paragraph(f"单元 {task_index} · {xml_escape(block['chapter_title'])} · {xml_escape(block['unit_title'])}", task_header_style),
            Paragraph(
                f"到期原因：{xml_escape(block['due_reason'] or '未标注')}  ·  学科：{xml_escape(block['book'] or '未标注')}",
                task_meta_style,
            ),
        ]
        if block.get("summary"):
            task_flowables.append(Paragraph(f"提要：{_paragraphize_pdf_text(block['summary'])}", prompt_hint_style))
        for question in block["questions"]:
            task_flowables.extend([
                Paragraph(f"第 {question['position']} 题", question_meta_style),
                Paragraph(_paragraphize_pdf_text(question["prompt"]), question_text_style),
                build_answer_box(question_column_width, line_count=4),
                Spacer(1, 3 * mm),
            ])
        task_flowables.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#D7DEE7"), spaceBefore=0, spaceAfter=4))
        story.append(KeepTogether(task_flowables))

    story.extend([
        NextPageTemplate("appendix"),
        PageBreak(),
        Paragraph("答案解析附页", appendix_title_style),
        Paragraph("附页用于打印后对照复盘，重点看得分点是否覆盖、解析是否理解、原文定位能否回到知识来源。", subtitle_style),
    ])

    for task_index, block in enumerate(blocks, start=1):
        story.append(Paragraph(f"单元 {task_index} · {xml_escape(block['chapter_title'])} · {xml_escape(block['unit_title'])}", appendix_task_style))
        story.append(Paragraph(f"到期原因：{xml_escape(block['due_reason'] or '未标注')}  ·  学科：{xml_escape(block['book'] or '未标注')}", task_meta_style))
        if block.get("summary"):
            story.append(Paragraph(f"提要：{_paragraphize_pdf_text(block['summary'])}", appendix_item_style))
        for question in block["questions"]:
            key_points = list(question.get("key_points") or [])
            appendix_block = [
                Paragraph(f"第 {question['position']} 题 · 题干：{_paragraphize_pdf_text(question['prompt'])}", appendix_item_style),
                Paragraph(f"参考答案：{_paragraphize_pdf_text(question['reference_answer'])}", appendix_answer_style),
            ]
            if key_points:
                appendix_block.append(
                    Paragraph(
                        f"得分要点：{_paragraphize_pdf_text('；'.join(str(point).strip() for point in key_points if str(point).strip()))}",
                        appendix_keypoint_style,
                    )
                )
            if question.get("explanation"):
                appendix_block.append(Paragraph(f"解析：{_paragraphize_pdf_text(question['explanation'])}", appendix_item_style))
            if question.get("source_excerpt"):
                appendix_block.append(Paragraph(f"原文定位：{_paragraphize_pdf_text(question['source_excerpt'])}", appendix_source_style))
            appendix_block.extend([
                Spacer(1, 1.5 * mm),
                HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceBefore=0, spaceAfter=4),
            ])
            story.append(KeepTogether(appendix_block))

    chapter_appendix_blocks: list[dict[str, Any]] = []
    seen_chapter_versions: set[tuple[str, str]] = set()
    for block in blocks:
        chapter_key = (
            str(block.get("chapter_title") or ""),
            _normalize_match_key(str(block.get("source_content") or "")),
        )
        if chapter_key in seen_chapter_versions:
            continue
        seen_chapter_versions.add(chapter_key)
        chapter_appendix_blocks.append(block)

    if chapter_appendix_blocks:
        story.extend([
            PageBreak(),
            Paragraph("整章原文附录", appendix_title_style),
            Paragraph("附录保留整章原文，避免只看到切片后的局部内容。做题后可回看整章逻辑链和知识点分布。", subtitle_style),
        ])
        for chapter_index, block in enumerate(chapter_appendix_blocks, start=1):
            story.append(Paragraph(f"章节 {chapter_index} · {xml_escape(block['chapter_title'])}", appendix_task_style))
            story.append(Paragraph(f"学科：{xml_escape(block['book'] or '未标注')}", task_meta_style))
            if block.get("summary"):
                story.append(Paragraph(f"提要：{_paragraphize_pdf_text(block['summary'])}", appendix_item_style))
            story.append(Paragraph(f"整章原文：{_paragraphize_pdf_text(block.get('source_content') or block.get('excerpt') or '')}", appendix_source_style))
            story.extend([
                Spacer(1, 2 * mm),
                HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1"), spaceBefore=0, spaceAfter=4),
            ])

    doc.build(story)
    return buffer.getvalue()


async def export_today_review_pdf(
    db: Session,
    *,
    actor_key: str,
    target_date: Optional[date] = None,
    time_budget_minutes: int = DEFAULT_REVIEW_TIME_BUDGET_MINUTES,
) -> bytes:
    plan = ensure_daily_review_plan(
        db,
        actor_key=actor_key,
        target_date=target_date,
        time_budget_minutes=time_budget_minutes,
    )
    db.commit()
    task_ids = [int(item["task_id"]) for item in plan["tasks"]]
    if not task_ids:
        raise HTTPException(status_code=404, detail="今天没有可导出的复习内容")

    tasks: list[ChapterReviewTask] = []
    for task_id in task_ids:
        tasks.append(await ensure_task_questions(db, actor_key=actor_key, task_id=task_id))

    return build_review_pdf(
        review_date=target_date or date.today(),
        tasks=tasks,
        time_budget_minutes=plan["estimated_total_minutes"] or time_budget_minutes,
    )
