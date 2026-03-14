import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Chapter
from learning_tracking_models import (
    DailyLearningLog,
    LearningActivity,
    LearningSession,
    QuestionRecord,
    SessionStatus,
    WrongAnswerV2,
    make_fingerprint,
)
from routers.learning_tracking import (
    CompleteSessionRequest,
    RecordQuestionRequest,
    complete_learning_session,
    get_knowledge_archive,
    get_knowledge_tree,
    get_stats,
    record_question_answer,
)


def run(coro):
    return asyncio.run(coro)


def make_db_session(tmp_path):
    db_path = tmp_path / "learning-tracking-regression.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)
    db = session_local()
    return db, engine


def make_session(db, session_id, **overrides):
    session = LearningSession(
        id=session_id,
        session_type=overrides.get("session_type", "exam"),
        chapter_id=overrides.get("chapter_id"),
        title=overrides.get("title", session_id),
        description=overrides.get("description", session_id),
        status=overrides.get("status", SessionStatus.IN_PROGRESS),
        started_at=overrides.get("started_at", datetime.now() - timedelta(minutes=10)),
        total_questions=overrides.get("total_questions", 0),
        answered_questions=overrides.get("answered_questions", 0),
        correct_count=overrides.get("correct_count", 0),
        wrong_count=overrides.get("wrong_count", 0),
        sure_count=overrides.get("sure_count", 0),
        unsure_count=overrides.get("unsure_count", 0),
        no_count=overrides.get("no_count", 0),
        duration_seconds=overrides.get("duration_seconds", 0),
        score=overrides.get("score", 0),
        accuracy=overrides.get("accuracy", 0),
    )
    db.add(session)
    db.commit()
    return session


def test_record_question_answer_upserts_and_rebuilds_session_stats(tmp_path):
    db, engine = make_db_session(tmp_path)
    try:
        session = make_session(db, "session-upsert")

        first = RecordQuestionRequest(
            question_index=0,
            question_type="A1",
            difficulty="基础",
            question_text="房颤的首选治疗是什么？",
            options={"A": "控制心率", "B": "立即手术"},
            correct_answer="A",
            user_answer="B",
            is_correct=False,
            confidence="unsure",
            explanation="先控制心率。",
            key_point="房颤处理",
            time_spent_seconds=20,
        )
        second = RecordQuestionRequest(
            question_index=0,
            question_type="A1",
            difficulty="基础",
            question_text="房颤的首选治疗是什么？",
            options={"A": "控制心率", "B": "立即手术"},
            correct_answer="A",
            user_answer="A",
            is_correct=True,
            confidence="sure",
            explanation="先控制心率。",
            key_point="房颤处理",
            time_spent_seconds=18,
        )

        first_result = run(record_question_answer(session.id, first, db))
        second_result = run(record_question_answer(session.id, second, db))

        records = db.query(QuestionRecord).filter(
            QuestionRecord.session_id == session.id
        ).all()
        refreshed = db.get(LearningSession, session.id)

        assert first_result["updated"] is False
        assert second_result["updated"] is True
        assert len(records) == 1
        assert records[0].user_answer == "A"
        assert records[0].is_correct is True
        assert refreshed.answered_questions == 1
        assert refreshed.correct_count == 1
        assert refreshed.wrong_count == 0
        assert refreshed.sure_count == 1
        assert refreshed.unsure_count == 0
        assert refreshed.no_count == 0
    finally:
        db.close()
        engine.dispose()


def test_get_stats_uses_deduped_question_records_as_single_source(tmp_path):
    db, engine = make_db_session(tmp_path)
    try:
        started_at = datetime.now() - timedelta(days=1)
        session = make_session(
            db,
            "session-stats",
            started_at=started_at,
            total_questions=99,
            correct_count=50,
            wrong_count=49,
            sure_count=40,
            unsure_count=30,
            no_count=29,
            duration_seconds=180,
        )

        db.add_all([
            QuestionRecord(
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="基础",
                question_text="高血压首选药物是？",
                options={"A": "ACEI", "B": "青霉素"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="unsure",
                answered_at=started_at + timedelta(minutes=1),
            ),
            QuestionRecord(
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="基础",
                question_text="高血压首选药物是？",
                options={"A": "ACEI", "B": "青霉素"},
                correct_answer="A",
                user_answer="A",
                is_correct=True,
                confidence="sure",
                answered_at=started_at + timedelta(minutes=2),
            ),
            QuestionRecord(
                session_id=session.id,
                question_index=1,
                question_type="A2",
                difficulty="提高",
                question_text="咳嗽伴铁锈色痰提示什么？",
                options={"A": "肺炎球菌肺炎", "B": "哮喘"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="no",
                answered_at=started_at + timedelta(minutes=3),
            ),
        ])
        db.commit()

        stats = run(get_stats(period="all", db=db))
        day_key = started_at.strftime("%Y-%m-%d")

        assert stats["summary"]["total_questions"] == 2
        assert stats["summary"]["total_correct"] == 1
        assert stats["summary"]["sure_count"] == 1
        assert stats["summary"]["unsure_count"] == 0
        assert stats["summary"]["no_count"] == 1
        assert stats["daily_trend"][day_key]["questions"] == 2
        assert stats["daily_trend"][day_key]["correct"] == 1
        assert stats["sessions"][0]["correct_count"] == 1
        assert stats["sessions"][0]["wrong_count"] == 1
        assert stats["sessions"][0]["total_questions"] == 99
        assert stats["type_distribution"]["A1"]["count"] == 1
        assert stats["type_distribution"]["A2"]["count"] == 1
    finally:
        db.close()
        engine.dispose()


def test_complete_learning_session_updates_daily_log_idempotently(tmp_path):
    db, engine = make_db_session(tmp_path)
    try:
        started_at = datetime.now() - timedelta(minutes=15)
        session = make_session(
            db,
            "session-complete",
            started_at=started_at,
        )
        db.add(
            QuestionRecord(
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="基础",
                question_text="最常见休克类型是什么？",
                options={"A": "低血容量性休克", "B": "神经性休克"},
                correct_answer="A",
                user_answer="A",
                is_correct=True,
                confidence="sure",
                answered_at=started_at + timedelta(minutes=5),
            )
        )
        db.commit()

        body = CompleteSessionRequest(score=100, total_questions=1)
        first = run(complete_learning_session(session.id, body, db))
        second = run(complete_learning_session(session.id, body, db))

        log = db.query(DailyLearningLog).one()
        submit_activities = db.query(LearningActivity).filter(
            LearningActivity.session_id == session.id,
            LearningActivity.activity_type == "exam_submit",
        ).all()

        assert first["accuracy"] == 100.0
        assert second["accuracy"] == 100.0
        assert log.total_sessions == 1
        assert log.total_questions == 1
        assert log.total_correct == 1
        assert log.total_wrong == 0
        assert log.session_ids == [session.id]
        assert len(submit_activities) == 1
    finally:
        db.close()
        engine.dispose()


def test_knowledge_tree_falls_back_to_wrong_answer_chapter_mapping(tmp_path):
    db, engine = make_db_session(tmp_path)
    try:
        chapter = Chapter(
            id="internal_ch1",
            book="内科学",
            edition="第10版",
            chapter_number="1",
            chapter_title="心力衰竭",
        )
        db.add(chapter)
        session = make_session(
            db,
            "session-tree",
            chapter_id="uncategorized_ch0",
        )
        question_text = "心力衰竭最常见的诱因是什么？"
        question = QuestionRecord(
            session_id=session.id,
            question_index=0,
            question_type="A1",
            difficulty="基础",
            question_text=question_text,
            options={"A": "感染", "B": "运动"},
            correct_answer="A",
            user_answer="B",
            is_correct=False,
            confidence="unsure",
            answered_at=datetime.now(),
        )
        wrong_answer = WrongAnswerV2(
            question_fingerprint=make_fingerprint(question_text),
            question_text=question_text,
            options={"A": "感染", "B": "运动"},
            correct_answer="A",
            explanation="感染最常见。",
            key_point="心衰诱因",
            question_type="A1",
            difficulty="基础",
            chapter_id=chapter.id,
            error_count=1,
            encounter_count=1,
            linked_record_ids=[],
        )
        db.add_all([question, wrong_answer])
        db.commit()

        data = run(get_knowledge_tree(period="all", db=db))

        assert data["tree"]
        book_node = next(node for node in data["tree"] if node["name"] == "内科学")
        chapter_node = next(node for node in book_node["chapters"] if node["name"] == "1 心力衰竭")
        assert chapter_node["total"] == 1
        assert chapter_node["key_points"][0]["name"].startswith("考点待提取：")
    finally:
        db.close()
        engine.dispose()


def test_knowledge_archive_uses_question_text_fallback_instead_of_uncategorized(tmp_path):
    db, engine = make_db_session(tmp_path)
    try:
        session = make_session(db, "session-archive")
        db.add(
            QuestionRecord(
                session_id=session.id,
                question_index=0,
                question_type="A1",
                difficulty="基础",
                question_text="肺结核首选的一线药物组合是什么？",
                options={"A": "HRZE", "B": "阿司匹林"},
                correct_answer="A",
                user_answer="B",
                is_correct=False,
                confidence="no",
                answered_at=datetime.now(),
            )
        )
        db.commit()

        archive = run(get_knowledge_archive(db=db))

        assert archive["total_questions"] == 1
        assert archive["knowledge_points"][0]["name"].startswith("考点待提取：肺结核首选的一线药物")
        assert archive["knowledge_points"][0]["name"] != "未分类"
    finally:
        db.close()
        engine.dispose()
