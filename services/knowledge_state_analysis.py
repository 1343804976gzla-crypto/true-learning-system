from __future__ import annotations

import json
import inspect
import asyncio
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from learning_tracking_models import (
    KnowledgeStateEvent,
    KnowledgeStateProfile,
    LearningSession,
    QuestionRecord,
    WrongAnswerV2,
)
from services.data_identity import build_storage_scope_key
from utils.data_contracts import normalize_confidence


STATE_TRUE_MASTERY = "True Mastery"
STATE_ILLUSION = "Illusion of Competence"
STATE_LUCKY = "Lucky / Underconfident Correct"
STATE_WEAKNESS = "Conscious Weakness"
STATE_STUBBORN = "Stubborn Error"
STATE_EXAM_TRANSFER = "Exam Transfer Failure"

STATE_PRIORITY = {
    STATE_ILLUSION: 0,
    STATE_STUBBORN: 1,
    STATE_EXAM_TRANSFER: 2,
    STATE_WEAKNESS: 3,
    STATE_LUCKY: 4,
    STATE_TRUE_MASTERY: 5,
}

NEXT_ACTION_BY_STATE = {
    STATE_TRUE_MASTERY: {"type": "advance", "label": "Move to mixed transfer questions"},
    STATE_ILLUSION: {"type": "concept_rebuild", "label": "Rebuild the concept before more drills"},
    STATE_LUCKY: {"type": "explain_rule", "label": "Explain the rule in your own words"},
    STATE_WEAKNESS: {"type": "concept_patch", "label": "Patch the concept and retry similar questions"},
    STATE_STUBBORN: {"type": "redo_stubborn_errors", "label": "Break down the repeated wrong-answer pattern"},
    STATE_EXAM_TRANSFER: {"type": "exam_transfer_check", "label": "Practice exam-style transfer"},
}


class ApiHubKnowledgeStateLlm:
    def analyze_knowledge_state(self, evidence_packet: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            from services.api_hub.facade import get_ai_client
        except Exception:
            return None

        try:
            ai = get_ai_client()
            if ai is None or not hasattr(ai, "generate_json"):
                return None

            prompt = (
                "你是学习系统的知识状态分析助手。请基于下面的 evidence_packet 生成知识状态弹窗文案。\n"
                "只输出 JSON，不要输出 Markdown、解释文字或代码块。\n"
                "不要编造 evidence_packet 中不存在的做题证据、错题记忆或置信度。\n"
                "必须解释每个知识点 previous_state -> current_state 的状态变化原因。\n"
                "如果出现高自信答错（sure_wrong），current_state 不能是 True Mastery，"
                "应保持或解释为 Illusion of Competence。\n\n"
                f"evidence_packet:\n{json.dumps(evidence_packet, ensure_ascii=False)}"
            )
            schema = {
                "type": "object",
                "properties": {
                    "popup_title": {"type": "string"},
                    "overall_summary": {"type": "string"},
                    "overall_trend": {"type": "string"},
                    "cards": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "knowledge_point": {"type": "string"},
                                "previous_state": {"type": ["string", "null"]},
                                "current_state": {"type": "string"},
                                "state_confidence": {"type": "string"},
                                "transition": {"type": "string"},
                                "interesting_insight": {"type": "string"},
                                "evidence_summary": {"type": "array", "items": {"type": "string"}},
                                "next_action": {"type": "object"},
                            },
                        },
                    },
                    "low_reliability_notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["popup_title", "overall_summary", "overall_trend", "cards"],
            }
            result = ai.generate_json(
                prompt,
                schema,
                max_tokens=2200,
                temperature=0.35,
                use_heavy=False,
                timeout=45,
            )
            if inspect.isawaitable(result):
                result = _run_awaitable(result)
            return result if isinstance(result, dict) else None
        except Exception:
            return None


def _run_awaitable(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    outcome: Dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["result"] = asyncio.run(awaitable)
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("result")


def analyze_completed_session(
    db: Session,
    session_id: str,
    *,
    scope_key: Optional[str] = None,
    llm_client: Any = None,
) -> Dict[str, Any]:
    session = db.query(LearningSession).filter(LearningSession.id == session_id).one_or_none()
    if session is None:
        raise ValueError("session_not_found")

    records = _latest_session_records(db, session_id)
    if not records:
        raise ValueError("session_has_no_question_records")

    resolved_scope_key = _resolve_scope_key(session, scope_key)
    grouped_records = _group_records(records)
    knowledge_points = list(grouped_records)
    profiles = _load_profiles(db, resolved_scope_key, knowledge_points)
    wrong_memory = _load_wrong_answer_memory(db, resolved_scope_key, knowledge_points)

    fallback_cards: List[Dict[str, Any]] = []
    evidence_points: List[Dict[str, Any]] = []
    low_reliability_notes: List[str] = []

    for knowledge_point, point_records in grouped_records.items():
        metrics = _metrics_for_records(point_records)
        metrics.update(_empty_wrong_memory())
        metrics.update(wrong_memory.get(knowledge_point, {}))

        previous_profile = profiles.get(knowledge_point)
        previous_state = previous_profile.current_state if previous_profile else None
        scored = _score_state(metrics)
        transition = _transition(previous_state, scored["current_state"])
        card = _fallback_card(
            knowledge_point=knowledge_point,
            previous_state=previous_state,
            current_state=scored["current_state"],
            state_confidence=scored["state_confidence"],
            transition=transition,
            metrics=metrics,
            guardrail_flags=scored["guardrail_flags"],
        )
        fallback_cards.append(card)

        evidence_points.append(
            {
                "knowledge_point": knowledge_point,
                "previous_state": previous_state,
                "current_state": scored["current_state"],
                "state_confidence": scored["state_confidence"],
                "transition": transition,
                "metrics": metrics,
                "guardrail_flags": scored["guardrail_flags"],
            }
        )
        if "confidence_missing" in scored["guardrail_flags"]:
            low_reliability_notes.append(f"{knowledge_point}: confidence data is missing for all attempts.")

    fallback_cards.sort(key=lambda item: STATE_PRIORITY.get(item["current_state"], 99))
    evidence_packet = {
        "analysis_context": {
            "trigger": "practice_completed",
            "session_id": session_id,
            "scope_key": resolved_scope_key,
            "generated_at": datetime.now().isoformat(),
        },
        "session_summary": _session_summary(session, records),
        "knowledge_points": evidence_points,
    }

    llm_payload = _try_llm_analysis(llm_client, evidence_packet)
    fallback_used = llm_payload is None
    if fallback_used:
        cards = fallback_cards
        popup_title = _fallback_title(cards)
        overall_summary = "System updated dynamic knowledge states from this completed practice session."
        overall_trend = _overall_trend(cards)
    else:
        cards = _validated_llm_cards(llm_payload or {}, fallback_cards)
        popup_title = str((llm_payload or {}).get("popup_title") or _fallback_title(cards))
        overall_summary = str(
            (llm_payload or {}).get("overall_summary")
            or "System updated dynamic knowledge states from this completed practice session."
        )
        overall_trend = str((llm_payload or {}).get("overall_trend") or _overall_trend(cards))
        low_reliability_notes.extend(
            str(item) for item in ((llm_payload or {}).get("low_reliability_notes") or [])
        )

    persisted_cards: List[Dict[str, Any]] = []
    evidence_by_point = {item["knowledge_point"]: item for item in evidence_points}
    for card in cards:
        point_evidence = evidence_by_point[card["knowledge_point"]]
        existing_event = _load_existing_event(
            db,
            scope_key=resolved_scope_key,
            session_id=session_id,
            knowledge_point=card["knowledge_point"],
        )
        if existing_event is not None:
            persisted_card = _card_from_existing_event(existing_event, fallback_card=card)
            persisted_card["event_id"] = existing_event.id
            persisted_cards.append(persisted_card)
            continue

        profile = _upsert_profile(
            db,
            session=session,
            scope_key=resolved_scope_key,
            knowledge_point=card["knowledge_point"],
            current_state=card["current_state"],
            state_confidence=card["state_confidence"],
            transition=card["transition"],
            metrics=point_evidence["metrics"],
        )
        event = KnowledgeStateEvent(
            profile_id=profile.id,
            user_id=session.user_id,
            device_id=session.device_id,
            scope_key=resolved_scope_key,
            session_id=session_id,
            knowledge_point=card["knowledge_point"],
            previous_state=card["previous_state"],
            current_state=card["current_state"],
            transition=card["transition"],
            state_confidence=card["state_confidence"],
            evidence_packet=evidence_packet,
            llm_analysis={"fallback_used": fallback_used, "card": card},
            guardrail_flags=card["guardrail_flags"],
        )
        db.add(event)
        db.flush()
        persisted_card = dict(card)
        persisted_card["event_id"] = event.id
        persisted_cards.append(persisted_card)

    db.commit()

    return {
        "analysis_id": str(persisted_cards[0]["event_id"]) if persisted_cards else None,
        "popup_title": popup_title,
        "overall_summary": overall_summary,
        "overall_trend": overall_trend,
        "cards": persisted_cards,
        "low_reliability_notes": low_reliability_notes,
        "fallback_used": fallback_used,
    }


def _resolve_scope_key(session: LearningSession, explicit_scope_key: Optional[str]) -> str:
    if explicit_scope_key:
        return explicit_scope_key
    if session.user_id or session.device_id:
        return build_storage_scope_key(user_id=session.user_id, device_id=session.device_id)
    return "anonymous"


def _latest_session_records(db: Session, session_id: str) -> List[QuestionRecord]:
    ranked = (
        db.query(
            QuestionRecord.id.label("id"),
            func.row_number()
            .over(
                partition_by=(QuestionRecord.session_id, QuestionRecord.question_index),
                order_by=(
                    case((QuestionRecord.answered_at.is_(None), 1), else_=0),
                    QuestionRecord.answered_at.desc(),
                    QuestionRecord.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .filter(QuestionRecord.session_id == session_id)
        .subquery()
    )
    latest_ids = db.query(ranked.c.id).filter(ranked.c.row_number == 1).subquery()
    return (
        db.query(QuestionRecord)
        .join(latest_ids, QuestionRecord.id == latest_ids.c.id)
        .order_by(QuestionRecord.question_index.asc(), QuestionRecord.id.asc())
        .all()
    )


def _record_knowledge_point(record: QuestionRecord) -> str:
    for value in (record.primary_key_point, record.key_point, record.question_text):
        text = str(value or "").strip()
        if text:
            return text[:160]
    return "Unnamed knowledge point"


def _group_records(records: List[QuestionRecord]) -> Dict[str, List[QuestionRecord]]:
    grouped: Dict[str, List[QuestionRecord]] = defaultdict(list)
    for record in records:
        grouped[_record_knowledge_point(record)].append(record)
    return dict(grouped)


def _load_profiles(
    db: Session,
    scope_key: str,
    knowledge_points: List[str],
) -> Dict[str, KnowledgeStateProfile]:
    if not knowledge_points:
        return {}
    profiles = (
        db.query(KnowledgeStateProfile)
        .filter(
            KnowledgeStateProfile.scope_key == scope_key,
            KnowledgeStateProfile.knowledge_point.in_(knowledge_points),
        )
        .all()
    )
    return {profile.knowledge_point: profile for profile in profiles}


def _empty_wrong_memory() -> Dict[str, Any]:
    return {
        "wrong_memory_error_count": 0,
        "wrong_memory_encounter_count": 0,
        "wrong_memory_retry_count": 0,
        "last_retry_correct": None,
        "last_retry_confidence": None,
        "severity_tags": [],
    }


def _load_wrong_answer_memory(
    db: Session,
    scope_key: str,
    knowledge_points: List[str],
) -> Dict[str, Dict[str, Any]]:
    if not knowledge_points:
        return {}

    rows = (
        db.query(WrongAnswerV2)
        .filter(
            WrongAnswerV2.scope_key == scope_key,
            WrongAnswerV2.key_point.in_(knowledge_points),
        )
        .all()
    )
    memory: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key_point = str(row.key_point or "").strip()
        if not key_point:
            continue
        item = memory.setdefault(key_point, _empty_wrong_memory())
        item["wrong_memory_error_count"] += int(row.error_count or 0)
        item["wrong_memory_encounter_count"] += int(row.encounter_count or 0)
        item["wrong_memory_retry_count"] += int(row.retry_count or 0)
        if row.last_retry_correct is not None:
            item["last_retry_correct"] = bool(row.last_retry_correct)
        if row.last_retry_confidence:
            item["last_retry_confidence"] = normalize_confidence(row.last_retry_confidence)
        if row.severity_tag and row.severity_tag not in item["severity_tags"]:
            item["severity_tags"].append(row.severity_tag)
    return memory


def _metrics_for_records(records: List[QuestionRecord]) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "attempt_count": len(records),
        "correct_count": 0,
        "wrong_count": 0,
        "accuracy": 0.0,
        "sure_count": 0,
        "unsure_count": 0,
        "no_count": 0,
        "confidence_missing_count": 0,
        "sure_wrong_count": 0,
        "sure_correct_count": 0,
        "low_confidence_correct_count": 0,
        "low_confidence_wrong_count": 0,
        "avg_time_spent_seconds": 0.0,
        "answer_change_count": 0,
    }
    total_time = 0
    for record in records:
        is_correct = bool(record.is_correct)
        confidence = normalize_confidence(record.confidence)
        metrics["correct_count" if is_correct else "wrong_count"] += 1

        if confidence == "sure":
            metrics["sure_count"] += 1
            metrics["sure_correct_count" if is_correct else "sure_wrong_count"] += 1
        elif confidence == "unsure":
            metrics["unsure_count"] += 1
            metrics["low_confidence_correct_count" if is_correct else "low_confidence_wrong_count"] += 1
        elif confidence == "no":
            metrics["no_count"] += 1
            metrics["low_confidence_correct_count" if is_correct else "low_confidence_wrong_count"] += 1
        else:
            metrics["confidence_missing_count"] += 1

        total_time += int(record.time_spent_seconds or 0)
        if isinstance(record.answer_changes, list):
            metrics["answer_change_count"] += len(record.answer_changes)

    attempt_count = int(metrics["attempt_count"])
    if attempt_count:
        metrics["accuracy"] = round(float(metrics["correct_count"]) / attempt_count, 4)
        metrics["avg_time_spent_seconds"] = round(float(total_time) / attempt_count, 1)
    return metrics


def _score_state(metrics: Dict[str, Any]) -> Dict[str, Any]:
    guardrail_flags: List[str] = []
    attempt_count = int(metrics.get("attempt_count") or 0)
    confidence_missing = int(metrics.get("confidence_missing_count") or 0)
    wrong_memory_errors = int(metrics.get("wrong_memory_error_count") or 0)
    retry_count = int(metrics.get("wrong_memory_retry_count") or 0)
    last_retry_correct = metrics.get("last_retry_correct")

    if confidence_missing >= max(attempt_count, 1):
        guardrail_flags.append("confidence_missing")
    if int(metrics.get("sure_wrong_count") or 0) > 0:
        guardrail_flags.append("sure_wrong")
    if wrong_memory_errors >= 3 and (retry_count == 0 or last_retry_correct is False):
        guardrail_flags.append("stubborn_memory")

    if "sure_wrong" in guardrail_flags:
        current_state = STATE_ILLUSION
    elif "stubborn_memory" in guardrail_flags:
        current_state = STATE_STUBBORN
    elif int(metrics.get("sure_correct_count") or 0) >= 2 and float(metrics.get("accuracy") or 0.0) >= 0.8:
        current_state = STATE_TRUE_MASTERY
    elif int(metrics.get("low_confidence_correct_count") or 0) > 0 and int(metrics.get("wrong_count") or 0) == 0:
        current_state = STATE_LUCKY
    elif int(metrics.get("low_confidence_wrong_count") or 0) > 0 or int(metrics.get("wrong_count") or 0) > 0:
        current_state = STATE_WEAKNESS
    elif int(metrics.get("correct_count") or 0) > 0:
        current_state = STATE_LUCKY if "confidence_missing" in guardrail_flags else STATE_TRUE_MASTERY
    else:
        current_state = STATE_WEAKNESS

    evidence_units = attempt_count + min(wrong_memory_errors, 3)
    if confidence_missing >= max(attempt_count, 1):
        state_confidence = "low"
    elif evidence_units >= 5:
        state_confidence = "high"
    elif evidence_units >= 3:
        state_confidence = "medium"
    else:
        state_confidence = "low"

    return {
        "current_state": current_state,
        "state_confidence": state_confidence,
        "guardrail_flags": guardrail_flags,
    }


def _transition(previous_state: Optional[str], current_state: str) -> str:
    if previous_state is None:
        return "first_observed"
    if previous_state == STATE_ILLUSION and current_state == STATE_WEAKNESS:
        return "calibration_improved"
    if previous_state == STATE_LUCKY and current_state == STATE_TRUE_MASTERY:
        return "mastery_stabilized"
    if previous_state == STATE_TRUE_MASTERY and current_state in {
        STATE_ILLUSION,
        STATE_WEAKNESS,
        STATE_STUBBORN,
        STATE_EXAM_TRANSFER,
    }:
        return "risk_regressed"
    if previous_state == current_state == STATE_STUBBORN:
        return "error_persisted"
    if previous_state == current_state:
        return "state_stable"
    return "state_changed"


def _next_action_for_state(state: str) -> Dict[str, str]:
    return dict(NEXT_ACTION_BY_STATE[state])


def _valid_llm_next_action(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    action_type = value.get("type")
    label = value.get("label")
    if not isinstance(action_type, str) or not action_type.strip():
        return None
    if not isinstance(label, str) or not label.strip():
        return None
    return {"type": action_type.strip(), "label": label.strip()}


def _load_existing_event(
    db: Session,
    *,
    scope_key: str,
    session_id: str,
    knowledge_point: str,
) -> Optional[KnowledgeStateEvent]:
    return (
        db.query(KnowledgeStateEvent)
        .filter(
            KnowledgeStateEvent.scope_key == scope_key,
            KnowledgeStateEvent.session_id == session_id,
            KnowledgeStateEvent.knowledge_point == knowledge_point,
        )
        .order_by(KnowledgeStateEvent.id.asc())
        .first()
    )


def _card_from_existing_event(
    event: KnowledgeStateEvent,
    *,
    fallback_card: Dict[str, Any],
) -> Dict[str, Any]:
    llm_analysis = event.llm_analysis if isinstance(event.llm_analysis, dict) else {}
    stored_card = llm_analysis.get("card") if isinstance(llm_analysis.get("card"), dict) else None
    card = dict(stored_card or fallback_card)
    card.setdefault("knowledge_point", event.knowledge_point)
    card.setdefault("previous_state", event.previous_state)
    card.setdefault("current_state", event.current_state)
    card.setdefault("state_confidence", event.state_confidence)
    card.setdefault("transition", event.transition)
    card.setdefault("guardrail_flags", event.guardrail_flags or [])
    card.setdefault("next_action", _next_action_for_state(card["current_state"]))
    return card


def _fallback_card(
    *,
    knowledge_point: str,
    previous_state: Optional[str],
    current_state: str,
    state_confidence: str,
    transition: str,
    metrics: Dict[str, Any],
    guardrail_flags: List[str],
) -> Dict[str, Any]:
    insight_by_transition = {
        "first_observed": "This is the first observed state for this knowledge point.",
        "calibration_improved": "虽然这次仍然答错，但自信判断变准了：你开始知道自己哪里不稳。",
        "mastery_stabilized": "Accuracy and confidence are starting to align into stable mastery.",
        "risk_regressed": "This point regressed from mastery and needs targeted review.",
        "error_persisted": "The same error pattern is still recurring.",
        "state_stable": "This point stayed in the same state after new evidence.",
        "state_changed": "New evidence changed the current state estimate.",
    }
    evidence_summary = [
        (
            f"{metrics.get('attempt_count', 0)} attempts, "
            f"{metrics.get('correct_count', 0)} correct, "
            f"{metrics.get('wrong_count', 0)} wrong"
        )
    ]
    if metrics.get("sure_wrong_count"):
        evidence_summary.append("High-confidence wrong answer observed")
    if metrics.get("low_confidence_correct_count"):
        evidence_summary.append("Low-confidence correct answer observed")
    if metrics.get("low_confidence_wrong_count"):
        evidence_summary.append("Low-confidence wrong answer observed")
    if metrics.get("wrong_memory_error_count"):
        evidence_summary.append(f"Wrong-answer memory has {metrics.get('wrong_memory_error_count')} errors")
    if metrics.get("confidence_missing_count"):
        evidence_summary.append("Confidence data missing")

    return {
        "knowledge_point": knowledge_point,
        "previous_state": previous_state,
        "current_state": current_state,
        "state_confidence": state_confidence,
        "transition": transition,
        "interesting_insight": insight_by_transition[transition],
        "evidence_summary": evidence_summary,
        "next_action": _next_action_for_state(current_state),
        "guardrail_flags": list(guardrail_flags),
    }


def _session_summary(session: LearningSession, records: List[QuestionRecord]) -> Dict[str, Any]:
    confidence_distribution = {"sure": 0, "unsure": 0, "no": 0, "missing": 0}
    for record in records:
        confidence = normalize_confidence(record.confidence)
        if confidence in {"sure", "unsure", "no"}:
            confidence_distribution[confidence] += 1
        else:
            confidence_distribution["missing"] += 1
    return {
        "session_type": session.session_type,
        "answered": len(records),
        "correct": sum(1 for record in records if bool(record.is_correct)),
        "wrong": sum(1 for record in records if not bool(record.is_correct)),
        "confidence_distribution": confidence_distribution,
    }


def _try_llm_analysis(llm_client: Any, evidence_packet: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if llm_client is None:
        return None
    try:
        if hasattr(llm_client, "analyze_knowledge_state"):
            result = llm_client.analyze_knowledge_state(evidence_packet)
        elif hasattr(llm_client, "generate_json"):
            result = llm_client.generate_json(
                prompt=(
                    "Analyze this learning evidence and return JSON only:\n"
                    + json.dumps(evidence_packet, ensure_ascii=False)
                ),
                schema={},
                max_tokens=1800,
                temperature=0.3,
            )
        else:
            return None
        if isinstance(result, str):
            result = json.loads(result)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _validated_llm_cards(
    llm_payload: Dict[str, Any],
    fallback_cards: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    fallback_by_point = {card["knowledge_point"]: card for card in fallback_cards}
    allowed_states = {
        STATE_TRUE_MASTERY,
        STATE_ILLUSION,
        STATE_LUCKY,
        STATE_WEAKNESS,
        STATE_STUBBORN,
        STATE_EXAM_TRANSFER,
    }
    cards: List[Dict[str, Any]] = []
    seen = set()
    for raw_card in llm_payload.get("cards") or []:
        if not isinstance(raw_card, dict):
            continue
        knowledge_point = str(raw_card.get("knowledge_point") or "")
        fallback = fallback_by_point.get(knowledge_point)
        if fallback is None:
            continue
        current_state = str(raw_card.get("current_state") or fallback["current_state"])
        if current_state not in allowed_states:
            current_state = fallback["current_state"]
        if "sure_wrong" in fallback["guardrail_flags"]:
            current_state = STATE_ILLUSION
        card = dict(fallback)
        fallback_state = card["current_state"]
        card["current_state"] = current_state
        if current_state != fallback_state:
            card["transition"] = _transition(card.get("previous_state"), current_state)
            card["next_action"] = _next_action_for_state(current_state)
        if raw_card.get("interesting_insight"):
            card["interesting_insight"] = str(raw_card["interesting_insight"])[:240]
        if isinstance(raw_card.get("evidence_summary"), list):
            card["evidence_summary"] = raw_card["evidence_summary"]
        if current_state == fallback_state:
            card["next_action"] = _valid_llm_next_action(raw_card.get("next_action")) or _next_action_for_state(current_state)
        cards.append(card)
        seen.add(knowledge_point)

    for fallback in fallback_cards:
        if fallback["knowledge_point"] not in seen:
            cards.append(fallback)
    return sorted(cards, key=lambda item: STATE_PRIORITY.get(item["current_state"], 99))


def _fallback_title(cards: List[Dict[str, Any]]) -> str:
    if any(card["current_state"] == STATE_ILLUSION for card in cards):
        return "High-confidence errors need attention"
    if any(card["transition"] == "calibration_improved" for card in cards):
        return "Calibration improved"
    return "Knowledge states updated"


def _overall_trend(cards: List[Dict[str, Any]]) -> str:
    transitions = {card["transition"] for card in cards}
    if "calibration_improved" in transitions or "mastery_stabilized" in transitions:
        return "mixed_but_improving"
    if "risk_regressed" in transitions:
        return "regression_risk"
    if "error_persisted" in transitions:
        return "persistent_errors"
    return "observed"


def _upsert_profile(
    db: Session,
    *,
    session: LearningSession,
    scope_key: str,
    knowledge_point: str,
    current_state: str,
    state_confidence: str,
    transition: str,
    metrics: Dict[str, Any],
) -> KnowledgeStateProfile:
    profile = (
        db.query(KnowledgeStateProfile)
        .filter(
            KnowledgeStateProfile.scope_key == scope_key,
            KnowledgeStateProfile.knowledge_point == knowledge_point,
        )
        .one_or_none()
    )
    if profile is None:
        profile = KnowledgeStateProfile(
            scope_key=scope_key,
            knowledge_point=knowledge_point,
            current_state=current_state,
            state_confidence=state_confidence,
        )
        db.add(profile)

    profile.user_id = session.user_id
    profile.device_id = session.device_id
    profile.current_state = current_state
    profile.state_confidence = state_confidence
    profile.stability_score = float(metrics.get("accuracy") or 0.0)
    profile.calibration_score = _calibration_score(metrics)
    profile.last_transition = transition
    profile.last_session_id = session.id
    profile.evidence_snapshot = metrics
    profile.updated_at = datetime.now()
    db.flush()
    return profile


def _calibration_score(metrics: Dict[str, Any]) -> float:
    calibrated = int(metrics.get("sure_correct_count") or 0) + int(
        metrics.get("low_confidence_wrong_count") or 0
    )
    attempts = int(metrics.get("attempt_count") or 0)
    if attempts <= 0:
        return 0.0
    return round(float(calibrated) / attempts, 4)
