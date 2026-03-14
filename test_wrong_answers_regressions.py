"""错题本近期回归问题的定向测试。"""

import sys
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, ".")

from learning_tracking_models import WrongAnswerRetry, WrongAnswerV2
from main import app
from models import Base, Chapter, get_db


_test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=_test_engine)
_TestSession = sessionmaker(bind=_test_engine)


@pytest.fixture
def db_session():
    """每个测试使用独立事务，允许路由内部正常 commit。"""
    connection = _test_engine.connect()
    transaction = connection.begin()
    session = _TestSession(bind=connection)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, trans):
        nonlocal nested
        if trans.nested and not trans._parent.nested:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """让 TestClient 使用测试数据库。"""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _create_wrong_answer(db: Session, **kwargs) -> WrongAnswerV2:
    defaults = {
        "question_fingerprint": f"fp_{datetime.now().timestamp()}_{id(kwargs)}",
        "question_text": "测试题目：哪个选项正确？",
        "options": {"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"},
        "correct_answer": "B",
        "explanation": "B 是正确答案。",
        "key_point": "测试知识点",
        "question_type": "A1",
        "difficulty": "基础",
        "severity_tag": "normal",
        "mastery_status": "active",
        "error_count": 1,
        "encounter_count": 1,
        "retry_count": 0,
        "sm2_ef": 2.5,
        "sm2_interval": 0,
        "sm2_repetitions": 0,
        "first_wrong_at": datetime.now(),
        "last_wrong_at": datetime.now(),
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    defaults.update(kwargs)
    wa = WrongAnswerV2(**defaults)
    db.add(wa)
    db.flush()
    return wa


def test_rescue_report_returns_markdown_content(client, db_session):
    """新增章节识别接口后，深水区求助报告不应退化成 null。"""
    wa = _create_wrong_answer(
        db_session,
        severity_tag="critical",
        variant_data={
            "variant_question": "变式题：哪个描述最准确？",
            "variant_options": {"A": "甲", "B": "乙"},
            "variant_answer": "A",
        },
    )
    db_session.add(
        WrongAnswerRetry(
            wrong_answer_id=wa.id,
            user_answer="B",
            is_correct=False,
            is_variant=True,
            rationale_text="我把病因和表现混在一起了。",
            ai_evaluation={
                "verdict": "failed",
                "reasoning_score": 32,
                "diagnosis": "关键概念混淆",
                "weak_links": ["测试知识点"],
            },
        )
    )
    db_session.commit()

    resp = client.post(f"/api/wrong-answers/{wa.id}/variant/rescue-report")

    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "markdown"
    assert "错题深水区求助" in data["content"]
    assert "关键概念混淆" in data["content"]


def test_recognize_chapters_updates_zero_and_unlinked_items(client, db_session):
    """章节识别接口应处理 chapter_id='0' 和未关联章节，并跳过已正常归类记录。"""
    db_session.add_all([
        Chapter(
            id="digestive_ch06",
            book="生理学",
            chapter_number="06",
            chapter_title="消化与吸收",
            concepts=[],
        ),
        Chapter(
            id="physiology_ch01",
            book="生理学",
            chapter_number="01",
            chapter_title="自动补齐章节(physiology_ch01)",
            concepts=[],
        ),
        Chapter(
            id="physio_ch01",
            book="生理学",
            chapter_number="01",
            chapter_title="绪论",
            concepts=[],
        ),
    ])
    uncategorized = _create_wrong_answer(
        db_session,
        chapter_id="0",
        key_point="胃液分泌",
        question_text="壁细胞分泌什么？",
    )
    unlinked = _create_wrong_answer(
        db_session,
        chapter_id="physiology_ch1",
        key_point="静息电位",
        question_text="静息电位由什么维持？",
    )
    categorized = _create_wrong_answer(
        db_session,
        chapter_id="digestive_ch06",
        key_point="心输出量",
        question_text="心输出量的定义是什么？",
    )
    db_session.commit()

    class DummyAI:
        async def generate_json(self, prompt, schema, **kwargs):
            if "胃液分泌" in prompt:
                return {"chapter_id": "digestive_ch06"}
            if "静息电位" in prompt:
                return {"chapter_id": "physio_ch01"}
            raise AssertionError(prompt)

    with patch("services.ai_client.get_ai_client", return_value=DummyAI()):
        resp = client.post("/api/wrong-answers/recognize-chapters?batch_size=20")

    assert resp.status_code == 200
    data = resp.json()
    assert data["recognized"] == 2
    assert resp.json()["failed"] == 0
    assert resp.json()["normalized"] == 0
    assert resp.json()["total"] == 2

    db_session.refresh(uncategorized)
    db_session.refresh(unlinked)
    db_session.refresh(categorized)
    assert uncategorized.chapter_id == "digestive_ch06"
    assert unlinked.chapter_id == "physio_ch01"
    assert categorized.chapter_id == "digestive_ch06"
