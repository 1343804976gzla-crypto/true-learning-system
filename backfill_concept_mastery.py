from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from learning_tracking_models import INVALID_CHAPTER_IDS, LearningSession, QuestionRecord, WrongAnswerV2
from models import Chapter, ConceptMastery, SessionLocal, TestRecord, WrongAnswer
from utils.data_contracts import normalize_confidence


UNCLASSIFIED_CHAPTER_ID = "uncategorized_ch0"
GENERIC_KEY_POINT_PATTERNS = (
    re.compile(r"^考点\d+$"),
    re.compile(r"^知识点\d+$"),
    re.compile(r"^题目\d+$"),
    re.compile(r"^未命名考点"),
    re.compile(r"^考点待提取"),
)

PLACEHOLDER_CONCEPT_PATTERNS = (
    re.compile(r"^(?:0_)?q\d+$", re.IGNORECASE),
    re.compile(r"^seed_\d+$", re.IGNORECASE),
    re.compile(r"^(?:unknown|uncategorized)", re.IGNORECASE),
    re.compile(r"^(?:\u65e0\u6cd5\u8bc6\u522b|\u672a\u8bc6\u522b\u7ae0\u8282)"),
    re.compile(r"^\u5185\u5bb9\u7f3a\u5931$"),
)


@dataclass(frozen=True)
class RecordSnapshot:
    record_id: int
    user_id: str | None
    device_id: str | None
    session_id: str
    chapter_id: str | None
    session_type: str | None
    key_point: str
    question_text: str
    difficulty: str | None
    confidence: str | None
    is_correct: bool
    answered_at: datetime | None


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    chapter_id: str | None
    session_type: str | None
    title: str
    knowledge_point: str
    uploaded_content: str


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _normalize_key_point(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_lookup_text(value: str | None) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _is_generic_key_point(value: str) -> bool:
    normalized = _normalize_key_point(value)
    if not normalized:
        return True
    return any(pattern.match(normalized) for pattern in GENERIC_KEY_POINT_PATTERNS)


def _normalize_confidence(value: str | None) -> str | None:
    normalized = normalize_confidence(value)
    if normalized in {"sure", "unsure", "no"}:
        return normalized
    return None


def _valid_chapter_id(value: str | None, chapter_ids: set[str]) -> str | None:
    chapter_id = str(value or "").strip()
    if not chapter_id or chapter_id in INVALID_CHAPTER_IDS:
        return None
    if chapter_id not in chapter_ids:
        return None
    return chapter_id


def _is_placeholder_chapter(chapter: Chapter | None) -> bool:
    if chapter is None:
        return True
    chapter_id = str(chapter.id or "").strip().lower()
    chapter_title = str(chapter.chapter_title or "").strip().lower()
    book = str(chapter.book or "").strip().lower()
    if chapter_id in {"0", UNCLASSIFIED_CHAPTER_ID}:
        return True
    return any(
        token in value
        for token in ("uncategorized", "unknown", "未分类", "无法识别", "未知")
        for value in (chapter_id, chapter_title, book)
    )


def _is_placeholder_concept_name(value: str | None) -> bool:
    normalized = _normalize_key_point(value)
    if not normalized:
        return True
    if _is_generic_key_point(normalized):
        return True
    return any(pattern.match(normalized) for pattern in PLACEHOLDER_CONCEPT_PATTERNS)


def _char_ngrams(value: str) -> set[str]:
    normalized = _normalize_lookup_text(value)
    if not normalized:
        return set()
    grams = {normalized}
    for size in (2, 3):
        if len(normalized) >= size:
            grams.update(normalized[i : i + size] for i in range(len(normalized) - size + 1))
    return grams


def _text_similarity(source: str, target_tokens: set[str]) -> float:
    source_tokens = _char_ngrams(source)
    if not source_tokens or not target_tokens:
        return 0.0
    overlap = len(source_tokens & target_tokens)
    if overlap == 0:
        return 0.0
    return overlap / max(1, min(len(source_tokens), len(target_tokens)))


def _difficulty_weight(value: str | None) -> float:
    normalized = str(value or "").strip()
    if normalized == "难题":
        return 1.35
    if normalized == "提高":
        return 1.15
    return 1.0


def _confidence_alignment(records: Iterable[RecordSnapshot]) -> float:
    scores = []
    for item in records:
        confidence = _normalize_confidence(item.confidence)
        if confidence == "sure":
            scores.append(1.0 if item.is_correct else -1.0)
        elif confidence == "unsure":
            scores.append(0.55 if item.is_correct else -0.35)
        elif confidence == "no":
            scores.append(0.35 if item.is_correct else -0.15)

    if not scores:
        return 0.5
    raw_score = sum(scores) / len(scores)
    return _clamp((raw_score + 1.0) / 2.0)


def _derived_concept_id(chapter_id: str, key_point: str) -> str:
    digest = hashlib.md5(f"{chapter_id}|{key_point}".encode("utf-8")).hexdigest()[:12]
    return f"{chapter_id}_tracked_{digest}"


def compute_mastery_metrics(records: list[RecordSnapshot]) -> dict[str, object]:
    if not records:
        raise ValueError("records must not be empty")

    ordered = sorted(records, key=lambda item: (item.answered_at or datetime.min, item.record_id))
    attempts = len(ordered)
    correct_count = sum(1 for item in ordered if item.is_correct)
    overall_accuracy = correct_count / attempts

    weighted_total = sum(_difficulty_weight(item.difficulty) for item in ordered)
    weighted_correct = sum(_difficulty_weight(item.difficulty) for item in ordered if item.is_correct)
    weighted_accuracy = weighted_correct / weighted_total if weighted_total else overall_accuracy

    recent_window = ordered[-5:]
    recent_accuracy = sum(1 for item in recent_window if item.is_correct) / len(recent_window)

    exam_records = [item for item in ordered if item.session_type == "exam"]
    exam_accuracy = (
        sum(1 for item in exam_records if item.is_correct) / len(exam_records)
        if exam_records
        else weighted_accuracy
    )

    hard_records = [item for item in ordered if str(item.difficulty or "").strip() == "难题"]
    hard_correct_rate = (
        sum(1 for item in hard_records if item.is_correct) / len(hard_records)
        if hard_records
        else weighted_accuracy
    )

    sure_wrong_rate = sum(1 for item in ordered if not item.is_correct and _normalize_confidence(item.confidence) == "sure") / attempts
    alignment = _confidence_alignment(ordered)

    retention = _clamp(
        0.50 * recent_accuracy
        + 0.35 * overall_accuracy
        + 0.15 * alignment
        - 0.10 * sure_wrong_rate
    )
    understanding = _clamp(
        0.40 * overall_accuracy
        + 0.25 * weighted_accuracy
        + 0.20 * alignment
        + 0.15 * recent_accuracy
        - 0.05 * sure_wrong_rate
    )
    application = _clamp(
        0.35 * weighted_accuracy
        + 0.25 * exam_accuracy
        + 0.20 * hard_correct_rate
        + 0.20 * recent_accuracy
        - 0.08 * sure_wrong_rate
    )

    last_answered_at = ordered[-1].answered_at or datetime.combine(date.today(), datetime.min.time())
    last_result_correct = ordered[-1].is_correct
    last_tested = last_answered_at.date()

    if not last_result_correct or overall_accuracy < 0.40:
        review_interval_days = 1
    elif recent_accuracy < 0.60 or sure_wrong_rate >= 0.20:
        review_interval_days = 2
    elif overall_accuracy < 0.75:
        review_interval_days = 4
    elif overall_accuracy < 0.90:
        review_interval_days = 7
    else:
        review_interval_days = 10

    return {
        "retention": round(retention, 4),
        "understanding": round(understanding, 4),
        "application": round(application, 4),
        "last_tested": last_tested,
        "next_review": last_tested + timedelta(days=review_interval_days),
        "attempts": attempts,
        "correct_count": correct_count,
    }


def _load_latest_question_records(db: Session, device_id: str) -> list[RecordSnapshot]:
    rows = (
        db.query(QuestionRecord, LearningSession)
        .join(LearningSession, LearningSession.id == QuestionRecord.session_id)
        .filter(QuestionRecord.device_id == device_id)
        .all()
    )

    latest_by_key: dict[tuple[str, int], RecordSnapshot] = {}
    for question_record, session in rows:
        snapshot = RecordSnapshot(
            record_id=int(question_record.id),
            user_id=question_record.user_id,
            device_id=question_record.device_id,
            session_id=question_record.session_id,
            chapter_id=session.chapter_id,
            session_type=session.session_type,
            key_point=_normalize_key_point(question_record.key_point),
            question_text=_normalize_key_point(question_record.question_text),
            difficulty=question_record.difficulty,
            confidence=question_record.confidence,
            is_correct=bool(question_record.is_correct),
            answered_at=question_record.answered_at,
        )
        key = (snapshot.session_id, int(question_record.question_index or 0))
        existing = latest_by_key.get(key)
        if existing is None or (snapshot.answered_at or datetime.min, snapshot.record_id) >= (
            existing.answered_at or datetime.min,
            existing.record_id,
        ):
            latest_by_key[key] = snapshot

    return list(latest_by_key.values())


def _load_session_snapshots(db: Session, device_id: str) -> dict[str, SessionSnapshot]:
    sessions = db.query(LearningSession).filter(LearningSession.device_id == device_id).all()
    return {
        session.id: SessionSnapshot(
            session_id=session.id,
            chapter_id=session.chapter_id,
            session_type=session.session_type,
            title=_normalize_key_point(session.title),
            knowledge_point=_normalize_key_point(session.knowledge_point),
            uploaded_content=_normalize_key_point((session.uploaded_content or "")[:1200]),
        )
        for session in sessions
    }


def _build_reference_maps(
    db: Session,
    device_id: str,
) -> tuple[dict[str, Chapter], dict[tuple[str, str], ConceptMastery], dict[str, ConceptMastery | None], dict[str, str]]:
    chapters = {chapter.id: chapter for chapter in db.query(Chapter).all()}
    chapter_ids = set(chapters.keys())

    concepts = db.query(ConceptMastery).filter(ConceptMastery.device_id == device_id).all()
    concepts_by_chapter_name: dict[tuple[str, str], ConceptMastery] = {}
    concepts_by_name: dict[str, list[ConceptMastery]] = defaultdict(list)
    for concept in concepts:
        normalized_name = _normalize_key_point(concept.name)
        concepts_by_chapter_name.setdefault((concept.chapter_id, normalized_name), concept)
        concepts_by_name[normalized_name].append(concept)

    unique_concepts_by_name: dict[str, ConceptMastery | None] = {}
    for name, items in concepts_by_name.items():
        real_items = [
            item
            for item in items
            if item.chapter_id and not _is_placeholder_chapter(chapters.get(item.chapter_id))
        ]
        chapter_set = {item.chapter_id for item in real_items if item.chapter_id}
        unique_concepts_by_name[name] = real_items[0] if len(chapter_set) == 1 and real_items else None

    wrong_answer_rows = (
        db.query(WrongAnswerV2.key_point, WrongAnswerV2.chapter_id)
        .filter(WrongAnswerV2.device_id == device_id)
        .all()
    )
    wrong_answer_chapter_map: dict[str, str] = {}
    grouped_wrong_answer_chapters: dict[str, set[str]] = defaultdict(set)
    for key_point, chapter_id in wrong_answer_rows:
        normalized_key_point = _normalize_key_point(key_point)
        valid_chapter = _valid_chapter_id(chapter_id, chapter_ids)
        if normalized_key_point and valid_chapter:
            grouped_wrong_answer_chapters[normalized_key_point].add(valid_chapter)

    for key_point, candidates in grouped_wrong_answer_chapters.items():
        if len(candidates) == 1:
            wrong_answer_chapter_map[key_point] = next(iter(candidates))

    return chapters, concepts_by_chapter_name, unique_concepts_by_name, wrong_answer_chapter_map


def _ensure_uncategorized_chapter(db: Session, chapters: dict[str, Chapter]) -> Chapter:
    existing = chapters.get(UNCLASSIFIED_CHAPTER_ID)
    if existing:
        return existing

    chapter = Chapter(
        id=UNCLASSIFIED_CHAPTER_ID,
        book="未分类",
        edition="Tracked",
        chapter_number="0",
        chapter_title="待归类知识点",
        concepts=[],
        first_uploaded=date.today(),
    )
    db.add(chapter)
    db.flush()
    chapters[chapter.id] = chapter
    return chapter


def _build_chapter_profiles(
    chapters: dict[str, Chapter],
    concepts_by_chapter_name: dict[tuple[str, str], ConceptMastery],
    records: list[RecordSnapshot],
) -> dict[str, set[str]]:
    profiles: dict[str, set[str]] = {}
    for chapter_id, chapter in chapters.items():
        if _is_placeholder_chapter(chapter):
            continue

        tokens = set()
        tokens |= _char_ngrams(chapter.book)
        tokens |= _char_ngrams(chapter.chapter_title)
        tokens |= _char_ngrams(chapter.id)
        profiles[chapter_id] = tokens

    for (chapter_id, key_point), _concept in concepts_by_chapter_name.items():
        if chapter_id in profiles and not _is_generic_key_point(key_point):
            profiles[chapter_id] |= _char_ngrams(key_point)

    for record in records:
        chapter_id = record.chapter_id
        if chapter_id in profiles and not _is_generic_key_point(record.key_point):
            profiles[chapter_id] |= _char_ngrams(record.key_point)
            profiles[chapter_id] |= _char_ngrams(record.question_text[:80])

    return profiles


def _infer_best_chapter_for_texts(
    texts: list[tuple[str, float]],
    chapter_profiles: dict[str, set[str]],
) -> str | None:
    scores: list[tuple[float, str]] = []
    for chapter_id, profile_tokens in chapter_profiles.items():
        score = 0.0
        for text, weight in texts:
            normalized = _normalize_key_point(text)
            if not normalized:
                continue
            score += _text_similarity(normalized, profile_tokens) * weight
        if score > 0:
            scores.append((score, chapter_id))

    if not scores:
        return None

    scores.sort(reverse=True)
    best_score, best_chapter_id = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    if best_score < 0.18:
        return None
    if second_score and best_score < second_score * 1.12:
        return None
    return best_chapter_id


def _infer_session_chapter_map(
    sessions: dict[str, SessionSnapshot],
    records: list[RecordSnapshot],
    chapter_profiles: dict[str, set[str]],
    chapter_ids: set[str],
    unique_concepts_by_name: dict[str, ConceptMastery | None],
    wrong_answer_chapter_map: dict[str, str],
) -> dict[str, str]:
    records_by_session: dict[str, list[RecordSnapshot]] = defaultdict(list)
    chapter_votes_by_session: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in records:
        records_by_session[record.session_id].append(record)
        chapter_id = _valid_chapter_id(record.chapter_id, chapter_ids)
        if not chapter_id:
            unique_concept = unique_concepts_by_name.get(record.key_point)
            if unique_concept and unique_concept.chapter_id in chapter_ids:
                chapter_id = unique_concept.chapter_id
            elif record.key_point in wrong_answer_chapter_map:
                chapter_id = wrong_answer_chapter_map[record.key_point]
        if chapter_id:
            chapter_votes_by_session[record.session_id][chapter_id] += 1

    inferred: dict[str, str] = {}
    for session_id, session in sessions.items():
        if _valid_chapter_id(session.chapter_id, chapter_ids):
            continue

        session_records = records_by_session.get(session_id, [])
        if not session_records:
            continue

        vote_counts = chapter_votes_by_session.get(session_id, {})
        if vote_counts:
            ranked_votes = sorted(vote_counts.items(), key=lambda item: (-item[1], item[0]))
            best_vote_chapter, best_vote_count = ranked_votes[0]
            total_votes = sum(vote_counts.values())
            if len(ranked_votes) == 1 or (total_votes and best_vote_count / total_votes >= 0.6):
                inferred[session_id] = best_vote_chapter
                continue

        texts: list[tuple[str, float]] = []
        if session.title:
            texts.append((session.title.replace("细节练习:", "").replace("医学考研模拟试卷（分段生成）", ""), 1.0))
        if session.knowledge_point:
            texts.append((session.knowledge_point, 1.2))
        if session.uploaded_content:
            texts.append((session.uploaded_content[:500], 1.0))

        seen_key_points: set[str] = set()
        for record in session_records:
            if record.key_point and record.key_point not in seen_key_points and not _is_generic_key_point(record.key_point):
                texts.append((record.key_point, 1.1))
                seen_key_points.add(record.key_point)
        for record in session_records[:4]:
            if record.question_text:
                texts.append((record.question_text[:120], 0.5))

        best_chapter_id = _infer_best_chapter_for_texts(texts, chapter_profiles)
        if best_chapter_id:
            inferred[session_id] = best_chapter_id

    return inferred


def _concept_signal_strength(concept: ConceptMastery) -> float:
    return (
        float(concept.retention or 0)
        + float(concept.understanding or 0)
        + float(concept.application or 0)
        + (1.0 if concept.last_tested else 0.0)
        + (1.0 if concept.next_review else 0.0)
    )


def _merge_concept_metrics(target: ConceptMastery, source: ConceptMastery) -> None:
    if _concept_signal_strength(source) <= _concept_signal_strength(target):
        return

    target.device_id = target.device_id or source.device_id
    target.user_id = target.user_id or source.user_id
    target.retention = source.retention
    target.understanding = source.understanding
    target.application = source.application
    target.last_tested = source.last_tested
    target.next_review = source.next_review


def _cleanup_placeholder_concepts(
    db: Session,
    device_id: str,
    chapters: dict[str, Chapter],
    concepts_by_chapter_name: dict[tuple[str, str], ConceptMastery],
    grouped_records: dict[tuple[str, str], list[RecordSnapshot]],
    unique_concepts_by_name: dict[str, ConceptMastery | None],
    wrong_answer_chapter_map: dict[str, str],
) -> tuple[int, int]:
    placeholder_concepts = (
        db.query(ConceptMastery)
        .filter(ConceptMastery.device_id == device_id)
        .filter(ConceptMastery.chapter_id.in_(list(INVALID_CHAPTER_IDS)))
        .all()
    )

    grouped_targets_by_name: dict[str, set[str]] = defaultdict(set)
    for chapter_id, key_point in grouped_records:
        if chapter_id and chapter_id not in INVALID_CHAPTER_IDS:
            grouped_targets_by_name[key_point].add(chapter_id)

    migrated_count = 0
    merged_count = 0

    for concept in placeholder_concepts:
        normalized_name = _normalize_key_point(concept.name)
        if not normalized_name or _is_placeholder_concept_name(normalized_name):
            continue

        target_chapter_id: str | None = None
        target_candidates = grouped_targets_by_name.get(normalized_name, set())
        if len(target_candidates) == 1:
            target_chapter_id = next(iter(target_candidates))

        if not target_chapter_id:
            unique_concept = unique_concepts_by_name.get(normalized_name)
            if unique_concept and unique_concept.chapter_id and not _is_placeholder_chapter(chapters.get(unique_concept.chapter_id)):
                target_chapter_id = unique_concept.chapter_id

        if not target_chapter_id and normalized_name in wrong_answer_chapter_map:
            candidate = wrong_answer_chapter_map[normalized_name]
            if not _is_placeholder_chapter(chapters.get(candidate)):
                target_chapter_id = candidate

        if not target_chapter_id or _is_placeholder_chapter(chapters.get(target_chapter_id)):
            continue

        target = concepts_by_chapter_name.get((target_chapter_id, normalized_name))
        if target and target.concept_id != concept.concept_id:
            _merge_concept_metrics(target, concept)
            db.query(TestRecord).filter(TestRecord.concept_id == concept.concept_id).update(
                {TestRecord.concept_id: target.concept_id},
                synchronize_session=False,
            )
            db.query(WrongAnswer).filter(WrongAnswer.concept_id == concept.concept_id).update(
                {WrongAnswer.concept_id: target.concept_id},
                synchronize_session=False,
            )
            concepts_by_chapter_name.pop((concept.chapter_id, normalized_name), None)
            db.delete(concept)
            merged_count += 1
            continue

        concepts_by_chapter_name.pop((concept.chapter_id, normalized_name), None)
        concept.chapter_id = target_chapter_id
        concepts_by_chapter_name[(target_chapter_id, normalized_name)] = concept
        migrated_count += 1

    return migrated_count, merged_count


def _resolve_chapter_id(
    record: RecordSnapshot,
    inferred_session_chapter_map: dict[str, str],
    chapter_ids: set[str],
    unique_concepts_by_name: dict[str, ConceptMastery | None],
    wrong_answer_chapter_map: dict[str, str],
) -> str | None:
    direct_chapter_id = _valid_chapter_id(record.chapter_id, chapter_ids)
    if direct_chapter_id:
        return direct_chapter_id

    session_chapter_id = inferred_session_chapter_map.get(record.session_id)
    if session_chapter_id and session_chapter_id in chapter_ids:
        return session_chapter_id

    unique_concept = unique_concepts_by_name.get(record.key_point)
    if unique_concept and unique_concept.chapter_id in chapter_ids:
        return unique_concept.chapter_id

    if record.key_point in wrong_answer_chapter_map:
        return wrong_answer_chapter_map[record.key_point]

    return None


def backfill_for_device(db: Session, device_id: str, *, apply_changes: bool) -> dict[str, object]:
    records = _load_latest_question_records(db, device_id)
    sessions = _load_session_snapshots(db, device_id)
    chapters, concepts_by_chapter_name, unique_concepts_by_name, wrong_answer_chapter_map = _build_reference_maps(db, device_id)
    chapter_ids = set(chapters.keys())
    chapter_profiles = _build_chapter_profiles(chapters, concepts_by_chapter_name, records)
    inferred_session_chapter_map = _infer_session_chapter_map(
        sessions,
        records,
        chapter_profiles,
        chapter_ids,
        unique_concepts_by_name,
        wrong_answer_chapter_map,
    )

    grouped_records: dict[tuple[str, str], list[RecordSnapshot]] = defaultdict(list)
    skipped_generic = 0
    uncategorized_records = 0

    for record in records:
        if not record.key_point:
            continue

        resolved_chapter_id = _resolve_chapter_id(
            record,
            inferred_session_chapter_map,
            chapter_ids,
            unique_concepts_by_name,
            wrong_answer_chapter_map,
        )
        unique_concept = unique_concepts_by_name.get(record.key_point)
        if _is_generic_key_point(record.key_point) and unique_concept is None:
            skipped_generic += 1
            continue

        if not resolved_chapter_id:
            _ensure_uncategorized_chapter(db, chapters)
            chapter_ids = set(chapters.keys())
            resolved_chapter_id = UNCLASSIFIED_CHAPTER_ID
            uncategorized_records += 1

        grouped_records[(resolved_chapter_id, record.key_point)].append(record)

    updated_count = 0
    created_count = 0
    summary_rows = []

    for (chapter_id, key_point), items in grouped_records.items():
        metrics = compute_mastery_metrics(items)
        concept = concepts_by_chapter_name.get((chapter_id, key_point))
        if concept is None:
            concept = ConceptMastery(
                concept_id=_derived_concept_id(chapter_id, key_point),
                chapter_id=chapter_id,
                name=key_point,
                device_id=device_id,
                user_id=next((item.user_id for item in items if item.user_id), None),
            )
            db.add(concept)
            concepts_by_chapter_name[(chapter_id, key_point)] = concept
            created_count += 1
        else:
            updated_count += 1

        concept.device_id = concept.device_id or device_id
        if not concept.user_id:
            concept.user_id = next((item.user_id for item in items if item.user_id), None)
        concept.retention = float(metrics["retention"])
        concept.understanding = float(metrics["understanding"])
        concept.application = float(metrics["application"])
        concept.last_tested = metrics["last_tested"]
        concept.next_review = metrics["next_review"]

        summary_rows.append(
            {
                "chapter_id": chapter_id,
                "key_point": key_point,
                "attempts": int(metrics["attempts"]),
                "correct_count": int(metrics["correct_count"]),
                "retention": float(metrics["retention"]),
                "understanding": float(metrics["understanding"]),
                "application": float(metrics["application"]),
                "next_review": str(metrics["next_review"]),
            }
        )

    migrated_placeholder_concepts = 0
    merged_placeholder_concepts = 0
    if apply_changes:
        migrated_placeholder_concepts, merged_placeholder_concepts = _cleanup_placeholder_concepts(
            db,
            device_id,
            chapters,
            concepts_by_chapter_name,
            grouped_records,
            unique_concepts_by_name,
            wrong_answer_chapter_map,
        )

    updated_sessions = 0
    if apply_changes and inferred_session_chapter_map:
        target_sessions = db.query(LearningSession).filter(LearningSession.id.in_(list(inferred_session_chapter_map.keys()))).all()
        for session in target_sessions:
            chapter_id = inferred_session_chapter_map.get(session.id)
            if chapter_id and _valid_chapter_id(chapter_id, chapter_ids):
                session.chapter_id = chapter_id
                updated_sessions += 1

    if apply_changes:
        db.commit()
    else:
        db.rollback()

    summary_rows.sort(key=lambda item: (item["retention"] + item["understanding"] + item["application"], item["attempts"]))

    return {
        "device_id": device_id,
        "source_records": len(records),
        "grouped_concepts": len(grouped_records),
        "updated": updated_count,
        "created": created_count,
        "migrated_placeholder_concepts": migrated_placeholder_concepts,
        "merged_placeholder_concepts": merged_placeholder_concepts,
        "updated_sessions": updated_sessions if apply_changes else len(inferred_session_chapter_map),
        "skipped_generic": skipped_generic,
        "uncategorized_records": uncategorized_records,
        "sample_weakest": summary_rows[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill concept_mastery from historical question_records.")
    parser.add_argument("--device-id", default="local-default", help="Target device_id")
    parser.add_argument("--apply", action="store_true", help="Persist changes to the database")
    args = parser.parse_args()

    with SessionLocal() as db:
        summary = backfill_for_device(db, args.device_id, apply_changes=args.apply)

    print(f"device_id={summary['device_id']}")
    print(f"source_records={summary['source_records']}")
    print(f"grouped_concepts={summary['grouped_concepts']}")
    print(f"updated={summary['updated']}")
    print(f"created={summary['created']}")
    print(f"migrated_placeholder_concepts={summary['migrated_placeholder_concepts']}")
    print(f"merged_placeholder_concepts={summary['merged_placeholder_concepts']}")
    print(f"updated_sessions={summary['updated_sessions']}")
    print(f"skipped_generic={summary['skipped_generic']}")
    print(f"uncategorized_records={summary['uncategorized_records']}")
    print("sample_weakest:")
    for item in summary["sample_weakest"]:
        print(item)

    if not args.apply:
        print("dry_run=true")


if __name__ == "__main__":
    main()
