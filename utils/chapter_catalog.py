from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


INVALID_BATCH_CHAPTER_IDS = {
    "",
    "0",
    "unknown_ch0",
    "未知_ch0",
    "无法识别_ch0",
    "未分类_ch0",
    "uncategorized_ch0",
}

_FULL_WIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
_SYNTHETIC_ID_PREFIXES = (
    "agent-",
    "seed-",
    "history-chapter",
    "backfill-",
    "contract-",
    "topic-",
    "tracking-upload-",
)
_SYNTHETIC_ID_PATTERNS = (
    re.compile(r"^chapter-[0-9a-f]{8,}$", re.IGNORECASE),
    re.compile(r"^[a-z0-9-]+-[0-9a-f]{8,}-chapter(?:-[a-z])?$", re.IGNORECASE),
)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def chinese_numeral_to_int(raw: str) -> int | None:
    mapping = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {
        "十": 10,
        "百": 100,
        "千": 1000,
    }
    text = _clean_text(raw).translate(_FULL_WIDTH_DIGIT_TRANS)
    if not text:
        return None
    if text.isdigit():
        return int(text)

    total = 0
    current = 0
    has_unit = False
    for ch in text:
        if ch in mapping:
            current = mapping[ch]
            continue
        if ch in units:
            has_unit = True
            unit = units[ch]
            if current == 0:
                current = 1
            total += current * unit
            current = 0
            continue
        return None

    total += current
    if total == 0 and has_unit:
        return None
    return total if total > 0 else None


def _natural_part_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    text = _clean_text(value).translate(_FULL_WIDTH_DIGIT_TRANS)
    if not text:
        return ((2, ""),)

    direct_number = chinese_numeral_to_int(text)
    if direct_number is not None:
        return ((0, direct_number),)

    key_parts: list[tuple[int, Any]] = []
    for piece in re.split(r"([0-9]+)", text):
        if not piece:
            continue
        if piece.isdigit():
            key_parts.append((0, int(piece)))
            continue
        number = chinese_numeral_to_int(piece)
        if number is not None:
            key_parts.append((0, number))
        else:
            key_parts.append((1, piece.lower()))
    return tuple(key_parts or [(2, "")])


def chapter_number_sort_key(chapter_number: Any) -> tuple[tuple[int, Any], ...]:
    text = _clean_text(chapter_number).translate(_FULL_WIDTH_DIGIT_TRANS)
    if not text:
        return ((2, ""),)

    normalized = re.sub(r"[._/]+", "-", text)
    parts = [part for part in re.split(r"-+", normalized) if part]
    if not parts:
        return ((2, ""),)

    key_parts: list[tuple[int, Any]] = []
    for part in parts:
        key_parts.extend(_natural_part_sort_key(part))
    return tuple(key_parts or [(2, "")])


def is_batch_catalog_chapter(
    *,
    chapter_id: Any,
    book: Any,
    chapter_number: Any,
    chapter_title: Any,
) -> bool:
    cid = _clean_text(chapter_id)
    subject = _clean_text(book)
    number = _clean_text(chapter_number)
    title = _clean_text(chapter_title)
    cid_lower = cid.lower()

    if cid in INVALID_BATCH_CHAPTER_IDS or cid.endswith("_ch0"):
        return False
    if number == "0":
        return False
    if subject in {"未分类", "unknown"}:
        return False
    if title.startswith("自动补齐章节") or title in {"待人工归类", "未知章节"}:
        return False
    if any(cid_lower.startswith(prefix) for prefix in _SYNTHETIC_ID_PREFIXES):
        return False
    if any(pattern.match(cid_lower) for pattern in _SYNTHETIC_ID_PATTERNS):
        return False
    return True


def _chapter_identity_key(row: Mapping[str, Any]) -> tuple[str, tuple[tuple[int, Any], ...], str]:
    return (
        _clean_text(row.get("book")).lower(),
        chapter_number_sort_key(row.get("chapter_number")),
        _clean_text(row.get("chapter_title")).lower(),
    )


def _chapter_preference_key(row: Mapping[str, Any]) -> tuple[int, int, int, str]:
    chapter_id = _clean_text(row.get("id")).lower()
    has_canonical_id = 0 if "_ch" in chapter_id else 1
    looks_generated = 1 if re.search(r"[0-9a-f]{8,}", chapter_id) else 0
    return (
        has_canonical_id,
        looks_generated,
        len(chapter_id),
        chapter_id,
    )


def chapter_row_sort_key(row: Mapping[str, Any]) -> tuple[str, tuple[tuple[int, Any], ...], str, str]:
    return (
        _clean_text(row.get("book")).lower(),
        chapter_number_sort_key(row.get("chapter_number")),
        _clean_text(row.get("chapter_title")).lower(),
        _clean_text(row.get("id")).lower(),
    )


def clean_batch_chapter_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    deduped: dict[tuple[str, tuple[tuple[int, Any], ...], str], dict[str, str]] = {}

    for raw in rows:
        row = {
            "id": _clean_text(raw.get("id")),
            "book": _clean_text(raw.get("book")),
            "chapter_number": _clean_text(raw.get("chapter_number")),
            "chapter_title": _clean_text(raw.get("chapter_title")),
        }
        if not is_batch_catalog_chapter(
            chapter_id=row["id"],
            book=row["book"],
            chapter_number=row["chapter_number"],
            chapter_title=row["chapter_title"],
        ):
            continue

        identity_key = _chapter_identity_key(row)
        existing = deduped.get(identity_key)
        if existing is None or _chapter_preference_key(row) < _chapter_preference_key(existing):
            deduped[identity_key] = row

    return sorted(deduped.values(), key=chapter_row_sort_key)
