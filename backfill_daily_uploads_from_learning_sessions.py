from __future__ import annotations

import argparse
from datetime import datetime
from typing import Iterable

from learning_tracking_models import LearningSession
from models import Chapter, DailyUpload, SessionLocal


def _study_date(session: LearningSession):
    if session.started_at:
        return session.started_at.date()
    if session.created_at:
        return session.created_at.date()
    return None


def _same_local_family(left: str | None, right: str | None) -> bool:
    left_normalized = str(left or "").strip()
    right_normalized = str(right or "").strip()
    local_tokens = {"local-default"}
    return (
        left_normalized in local_tokens or left_normalized.startswith("local-")
    ) and (
        right_normalized in local_tokens or right_normalized.startswith("local-")
    )


def _matches_actor(existing: DailyUpload, session: LearningSession) -> bool:
    existing_user = str(existing.user_id or "").strip()
    session_user = str(session.user_id or "").strip()
    if existing_user != session_user:
        return False

    existing_device = str(existing.device_id or "").strip()
    session_device = str(session.device_id or "").strip()
    if existing_device == session_device:
        return True
    if not session_user and _same_local_family(existing_device, session_device):
        return True
    return False


def _build_ai_extracted(session: LearningSession, chapter: Chapter | None, raw_content: str) -> dict:
    payload = {
        "book": getattr(chapter, "book", None) or "未识别",
        "chapter_title": getattr(chapter, "chapter_title", None) or (session.title or "未识别章节"),
        "chapter_id": str(session.chapter_id or ""),
        "main_topic": session.knowledge_point or "",
        "summary": raw_content[:160].strip(),
        "concepts": list(getattr(chapter, "concepts", None) or []),
    }
    if getattr(chapter, "chapter_number", None):
        payload["chapter_number"] = chapter.chapter_number
    if getattr(chapter, "edition", None):
        payload["edition"] = chapter.edition
    return payload


def iter_sessions(db) -> Iterable[LearningSession]:
    return (
        db.query(LearningSession)
        .filter(
            LearningSession.uploaded_content.isnot(None),
            LearningSession.uploaded_content != "",
        )
        .order_by(LearningSession.started_at.asc(), LearningSession.created_at.asc(), LearningSession.id.asc())
        .all()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily_uploads from learning_sessions.uploaded_content")
    parser.add_argument("--dry-run", action="store_true", help="Show planned inserts without writing")
    args = parser.parse_args()

    created = 0
    skipped = 0
    inspected = 0

    with SessionLocal() as db:
        chapters = {chapter.id: chapter for chapter in db.query(Chapter).all()}
        sessions = list(iter_sessions(db))
        for session in sessions:
            raw_content = str(session.uploaded_content or "").strip()
            study_date = _study_date(session)
            if not raw_content or study_date is None:
                skipped += 1
                continue

            inspected += 1
            candidates = (
                db.query(DailyUpload)
                .filter(
                    DailyUpload.date == study_date,
                    DailyUpload.raw_content == raw_content,
                )
                .all()
            )
            if any(_matches_actor(item, session) for item in candidates):
                skipped += 1
                continue

            chapter = chapters.get(str(session.chapter_id or "").strip())
            upload = DailyUpload(
                user_id=session.user_id,
                device_id=session.device_id,
                date=study_date,
                raw_content=raw_content,
                ai_extracted=_build_ai_extracted(session, chapter, raw_content),
                created_at=session.started_at or session.created_at or datetime.now(),
            )
            db.add(upload)
            created += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

    mode = "dry-run" if args.dry_run else "write"
    print(
        {
            "mode": mode,
            "sessions_checked": inspected,
            "created": created,
            "skipped": skipped,
        }
    )


if __name__ == "__main__":
    main()
