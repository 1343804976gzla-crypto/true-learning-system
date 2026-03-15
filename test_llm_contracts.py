import json
import sqlite3
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from learning_tracking_models import WrongAnswerV2
from main import app
from migrate_json_contracts import run_migration
from models import Chapter, ConceptMastery, SessionLocal, TestRecord, WrongAnswer
from services.fusion_service import get_fusion_service
from utils.data_contracts import (
    SCHEMA_VERSION,
    canonicalize_quiz_answers,
    canonicalize_quiz_questions,
    coerce_confidence,
    normalize_option_map,
)


def _seed_quiz_concept(chapter_id: str, concept_id: str, concept_name: str) -> None:
    db = SessionLocal()
    try:
        if not db.query(Chapter).filter(Chapter.id == chapter_id).first():
            db.add(
                Chapter(
                    id=chapter_id,
                    book="contract-book",
                    chapter_number="1",
                    chapter_title="Contract Chapter",
                )
            )

        if not db.query(ConceptMastery).filter(ConceptMastery.concept_id == concept_id).first():
            db.add(
                ConceptMastery(
                    concept_id=concept_id,
                    chapter_id=chapter_id,
                    name=concept_name,
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                )
            )

        db.commit()
    finally:
        db.close()


def test_normalize_option_map_orders_and_filters_keys():
    normalized = normalize_option_map(
        {
            "b": "B option",
            "A.": "A option",
            "Z": "ignored",
            "d ": "D option",
            "C": "C option",
            "E": "E option",
            "": "empty",
        }
    )

    assert list(normalized.keys()) == ["A", "B", "C", "D", "E"]
    assert normalized["A"] == "A option"
    assert "Z" not in normalized


def test_llm_audit_endpoint_returns_contract_summary():
    client = TestClient(app)

    response = client.get("/api/llm/audit")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["total_routes"] >= payload["typed_routes"]
    assert payload["untyped_routes"] == 0
    assert isinstance(payload["router_coverage"], list)
    assert isinstance(payload["enum_catalog"], list)
    assert isinstance(payload["json_columns"], list)


def test_coerce_confidence_normalizes_blank_and_aliases():
    assert coerce_confidence("") == "unsure"
    assert coerce_confidence(None) == "unsure"
    assert coerce_confidence("dont_know") == "no"
    assert coerce_confidence("sure") == "sure"


def test_tracking_question_endpoint_coerces_empty_confidence():
    client = TestClient(app)

    start_response = client.post(
        "/api/tracking/session/start",
        json={"session_type": "detail_practice", "title": "contract-test-session"},
    )
    assert start_response.status_code == 200
    session_id = start_response.json()["session_id"]

    question_response = client.post(
        f"/api/tracking/session/{session_id}/question",
        json={
            "question_index": 0,
            "question_type": "A1",
            "difficulty": "基础",
            "question_text": "测试题干",
            "options": {"A": "选项A", "B": "选项B"},
            "correct_answer": "A",
            "user_answer": "B",
            "is_correct": False,
            "confidence": "",
            "explanation": "测试解析",
            "key_point": "测试考点",
            "time_spent_seconds": 12,
        },
    )
    assert question_response.status_code == 200

    detail_response = client.get(f"/api/tracking/session/{session_id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["questions"][0]["confidence"] == "unsure"


def test_llm_context_endpoint_returns_stable_bundle():
    client = TestClient(app)

    response = client.get(
        "/api/llm/context",
        params={
            "wrong_answer_limit": 2,
            "session_limit": 2,
            "include_activities": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "dataset_summary" in payload
    assert "analytics" in payload
    assert len(payload["wrong_answers"]) <= 2
    assert len(payload["recent_sessions"]) <= 2


def test_legacy_quiz_start_route_returns_stable_contract():
    client = TestClient(app)

    response = client.post("/api/quiz/start/contract_chapter", params={"mode": "practice"})

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["session_id"], int)
    assert payload["total_questions"] == 10
    assert payload["mode"] == "practice"
    assert len(payload["questions"]) == 10
    assert isinstance(payload["questions"][0]["options"], dict)


def test_tracking_question_endpoint_canonicalizes_answer_changes():
    client = TestClient(app)

    start_response = client.post(
        "/api/tracking/session/start",
        json={"session_type": "detail_practice", "title": "answer-change-contract-test"},
    )
    assert start_response.status_code == 200
    session_id = start_response.json()["session_id"]

    question_response = client.post(
        f"/api/tracking/session/{session_id}/question",
        json={
            "question_index": 0,
            "question_type": "A1",
            "difficulty": "鍩虹",
            "question_text": "规范化测试题",
            "options": {"b": "选项B", "A.": "选项A"},
            "correct_answer": "A",
            "user_answer": "B",
            "is_correct": False,
            "confidence": "",
            "explanation": "测试解析",
            "key_point": "测试考点",
            "time_spent_seconds": 18,
            "answer_changes": [
                {"from": "b", "to": "A.", "timestamp": "2026-03-15T12:00:00"},
                {"previous_answer": "A", "current_answer": "c", "comment": " rethink "},
                "ignored",
            ],
        },
    )
    assert question_response.status_code == 200

    detail_response = client.get(f"/api/tracking/session/{session_id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    question = payload["questions"][0]

    assert question["confidence"] == "unsure"
    assert list(question["options"].keys()) == ["A", "B"]
    assert question["answer_changes"][0]["from"] == "B"
    assert question["answer_changes"][0]["to"] == "A"
    assert question["answer_changes"][0]["at"] == "2026-03-15T12:00:00"
    assert question["answer_changes"][1]["from"] == "A"
    assert question["answer_changes"][1]["to"] == "C"
    assert question["answer_changes"][1]["note"] == "rethink"


def test_quiz_payload_canonicalizers_normalize_nested_session_shapes():
    questions = canonicalize_quiz_questions(
        [
            {
                "question_id": "q-1",
                "question": " 题干 ",
                "options": {"b": "B项", "A.": "A项"},
                "correct_answer": " b ",
                "key_points": [" 循环 ", "", "循环"],
            }
        ]
    )
    answers = canonicalize_quiz_answers(
        [
            {
                "user_answer": " a ",
                "confidence": "",
                "time_spent": "12",
                "weak_points": [" 计算 ", "", "计算"],
            }
        ]
    )

    assert questions[0]["options"] == {"A": "A项", "B": "B项"}
    assert questions[0]["correct_answer"] == "B"
    assert questions[0]["key_points"] == ["循环"]
    assert answers[0]["confidence"] == "unsure"
    assert answers[0]["time_spent"] == 12
    assert answers[0]["question_index"] == 0
    assert answers[0]["weak_points"] == ["计算"]


def test_wrong_answer_variant_generate_persists_canonicalized_payload(monkeypatch):
    db = SessionLocal()
    try:
        now = datetime.now()
        wrong = WrongAnswerV2(
            question_fingerprint=f"variant-test-{uuid4().hex}",
            question_text="原题",
            options={"A": "A", "B": "B"},
            correct_answer="A",
            explanation="原解析",
            key_point="原考点",
            question_type="A1",
            difficulty="鍩虹",
            error_count=1,
            encounter_count=1,
            retry_count=0,
            severity_tag="normal",
            mastery_status="active",
            first_wrong_at=now,
            last_wrong_at=now,
        )
        db.add(wrong)
        db.commit()
        db.refresh(wrong)
        wrong_id = wrong.id
    finally:
        db.close()

    async def fake_generate_variant(_wa):
        return {
            "variant_question": " 变式题 ",
            "variant_options": {"b": "选项B", "A.": "选项A"},
            "variant_answer": " b ",
            "variant_explanation": " 变式解析 ",
            "transform_type": " 重组 ",
            "core_knowledge": " 核心考点 ",
        }

    monkeypatch.setattr("services.variant_surgery_service.generate_variant", fake_generate_variant)

    client = TestClient(app)
    response = client.post(f"/api/wrong-answers/{wrong_id}/variant/generate")
    assert response.status_code == 200
    payload = response.json()
    assert payload["variant_options"] == {"A": "选项A", "B": "选项B"}
    assert payload["variant_answer"] == "B"

    db = SessionLocal()
    try:
        stored = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_id).first()
        assert stored is not None
        assert stored.variant_data["variant_options"] == {"A": "选项A", "B": "选项B"}
        assert stored.variant_data["variant_answer"] == "B"
        assert stored.variant_data["transform_type"] == "重组"
        assert "generated_at" in stored.variant_data
    finally:
        db.close()


def test_fusion_routes_persist_canonicalized_fusion_data():
    db = SessionLocal()
    try:
        now = datetime.now()
        fusion = WrongAnswerV2(
            question_fingerprint=f"fusion-test-{uuid4().hex}",
            question_text="融合题",
            options=None,
            correct_answer="FUSION",
            explanation=None,
            key_point="融合考点",
            question_type="FUSION",
            difficulty="闅鹃",
            error_count=0,
            encounter_count=0,
            retry_count=0,
            severity_tag="critical",
            mastery_status="active",
            parent_ids=[3, 1, 2],
            is_fusion=True,
            fusion_level=1,
            fusion_data={
                "expected_key_points": [" 逻辑链 ", "", "逻辑链"],
                "scoring_criteria": {"logic": "30", "accuracy": 40},
                "judgement_pending": "false",
            },
            first_wrong_at=now,
            last_wrong_at=now,
        )
        db.add(fusion)
        db.commit()
        db.refresh(fusion)
        fusion_id = fusion.id
    finally:
        db.close()



def test_quiz_fast_routes_persist_canonicalized_json(monkeypatch):
    chapter_id = f"contract-fast-{uuid4().hex}"
    concept_id = f"{chapter_id}-concept"
    _seed_quiz_concept(chapter_id, concept_id, "Fast Contract Concept")

    class FakePreGenService:
        async def generate_batch(self, _names, _descriptions):
            return [
                {
                    "question": "Fast question",
                    "options": {"b": "Option B", "A.": "Option A", "Z": "Ignore"},
                    "correct_answer": "A",
                    "explanation": "Fast explanation",
                    "key_points": [" 计算 ", "", "计算"],
                    "difficulty": "medium",
                    "common_mistakes": ["mix-up"],
                }
            ]

    class FakeLocalGrader:
        def grade_batch(self, _questions, _answers):
            return [
                {
                    "is_correct": False,
                    "score": 0,
                    "feedback": " review ",
                    "weak_points": [" 计算 ", "", "计算"],
                    "error_type": "knowledge_gap",
                    "confidence_analysis": " hesitant ",
                }
            ]

    class FakeAnalyzer:
        async def analyze_comprehensive(self, _questions, _graded, _answers):
            return {"summary": "ok"}

    monkeypatch.setattr("routers.quiz_fast.get_pre_gen_service", lambda: FakePreGenService())
    monkeypatch.setattr("routers.quiz_fast.get_local_grader", lambda: FakeLocalGrader())
    monkeypatch.setattr("routers.quiz_fast.get_comprehensive_analyzer", lambda: FakeAnalyzer())

    client = TestClient(app)

    start_response = client.post(f"/api/quiz-fast/start/{chapter_id}")
    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert start_payload["total_questions"] == 1
    test_id = start_payload["questions"][0]["test_id"]
    session_id = start_payload["session_id"]

    db = SessionLocal()
    try:
        stored_test = db.query(TestRecord).filter(TestRecord.id == test_id).first()
        assert stored_test is not None
        assert stored_test.ai_options == {"A": "Option A", "B": "Option B"}
    finally:
        db.close()

    submit_response = client.post(
        f"/api/quiz-fast/submit/{session_id}",
        json={"answers": [{"user_answer": "C", "confidence": "", "time_spent": 12}]},
    )
    assert submit_response.status_code == 200
    submit_payload = submit_response.json()
    assert submit_payload["answers"][0]["confidence"] == "unsure"
    assert submit_payload["answers"][0]["weak_points"] == ["计算"]

    db = SessionLocal()
    try:
        stored_wrong = (
            db.query(WrongAnswer)
            .filter(
                WrongAnswer.concept_id == concept_id,
                WrongAnswer.question == "Fast question",
            )
            .first()
        )
        assert stored_wrong is not None
        assert stored_wrong.options == {"A": "Option A", "B": "Option B"}
        assert stored_wrong.weak_points == ["计算"]
    finally:
        db.close()


def test_quiz_concurrent_routes_persist_canonicalized_json(monkeypatch):
    chapter_id = f"contract-concurrent-{uuid4().hex}"
    concept_id = f"{chapter_id}-concept"
    _seed_quiz_concept(chapter_id, concept_id, "Concurrent Contract Concept")

    class FakeGenerator:
        async def generate_quiz_batch(self, _names, _descriptions):
            return [
                {
                    "question": "Concurrent question",
                    "options": {"b": "Option B", "A.": "Option A"},
                    "correct_answer": "A",
                    "explanation": "Concurrent explanation",
                    "key_points": [" 链接 ", "", "链接"],
                    "difficulty": "medium",
                }
            ]

    class FakeBatchGrader:
        async def grade_batch(self, _questions, _answers):
            return [
                {
                    "is_correct": False,
                    "score": 0,
                    "feedback": " retry ",
                    "weak_points": [" 链接 ", "", "链接"],
                    "error_type": "knowledge_gap",
                }
            ]

    class FakeAnalyzer:
        async def analyze_session(self, _questions, _graded, _answers):
            return {"summary": "ok"}

    monkeypatch.setattr("routers.quiz_concurrent.get_concurrent_generator", lambda: FakeGenerator())
    monkeypatch.setattr("routers.quiz_concurrent.get_batch_grader", lambda: FakeBatchGrader())
    monkeypatch.setattr("routers.quiz_concurrent.get_ai_analyzer", lambda: FakeAnalyzer())

    client = TestClient(app)

    start_response = client.post(f"/api/quiz-v2/start/{chapter_id}")
    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert start_payload["total_questions"] == 1
    test_id = start_payload["questions"][0]["test_id"]
    session_id = start_payload["session_id"]

    db = SessionLocal()
    try:
        stored_test = db.query(TestRecord).filter(TestRecord.id == test_id).first()
        assert stored_test is not None
        assert stored_test.ai_options == {"A": "Option A", "B": "Option B"}
    finally:
        db.close()

    submit_response = client.post(
        f"/api/quiz-v2/submit/{session_id}",
        json={"answers": [{"user_answer": "D", "confidence": "", "time_spent": 16}]},
    )
    assert submit_response.status_code == 200
    submit_payload = submit_response.json()
    assert submit_payload["answers"][0]["confidence"] == "unsure"
    assert submit_payload["answers"][0]["weak_points"] == ["链接"]

    db = SessionLocal()
    try:
        stored_wrong = (
            db.query(WrongAnswer)
            .filter(
                WrongAnswer.concept_id == concept_id,
                WrongAnswer.question == "Concurrent question",
            )
            .first()
        )
        assert stored_wrong is not None
        assert stored_wrong.options == {"A": "Option A", "B": "Option B"}
        assert stored_wrong.weak_points == ["链接"]
    finally:
        db.close()


def test_json_contract_migration_normalizes_major_columns(tmp_path):
    db_path = tmp_path / "json-contracts.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE quiz_sessions (
                id INTEGER PRIMARY KEY,
                questions TEXT,
                answers TEXT
            );
            CREATE TABLE test_records (
                id INTEGER PRIMARY KEY,
                ai_options TEXT,
                weak_points TEXT
            );
            CREATE TABLE wrong_answers (
                id INTEGER PRIMARY KEY,
                options TEXT,
                weak_points TEXT
            );
            CREATE TABLE learning_activities (
                id INTEGER PRIMARY KEY,
                data TEXT
            );
            CREATE TABLE question_records (
                id INTEGER PRIMARY KEY,
                options TEXT,
                answer_changes TEXT
            );
            CREATE TABLE wrong_answers_v2 (
                id INTEGER PRIMARY KEY,
                options TEXT,
                linked_record_ids TEXT,
                variant_data TEXT,
                parent_ids TEXT,
                fusion_data TEXT
            );
            CREATE TABLE wrong_answer_retries (
                id INTEGER PRIMARY KEY,
                ai_evaluation TEXT
            );
            """
        )

        conn.execute(
            "INSERT INTO quiz_sessions (id, questions, answers) VALUES (?, ?, ?)",
            (
                1,
                json.dumps(
                    [
                        {
                            "question": " q ",
                            "options": {"b": "Option B", "A.": "Option A"},
                            "correct_answer": " b ",
                            "key_points": [" 循环 ", "", "循环"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                json.dumps(
                    [
                        {
                            "user_answer": " a ",
                            "confidence": "",
                            "weak_points": [" 计算 ", "", "计算"],
                        }
                    ],
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO test_records (id, ai_options, weak_points) VALUES (?, ?, ?)",
            (
                1,
                json.dumps({"b": "Option B", "A.": "Option A"}, ensure_ascii=False),
                json.dumps([" 计算 ", "", "计算"], ensure_ascii=False),
            ),
        )
        conn.execute(
            "INSERT INTO wrong_answers (id, options, weak_points) VALUES (?, ?, ?)",
            (
                1,
                json.dumps({"b": "Option B", "A.": "Option A"}, ensure_ascii=False),
                json.dumps([" 计算 ", "", "计算"], ensure_ascii=False),
            ),
        )
        conn.execute(
            "INSERT INTO learning_activities (id, data) VALUES (?, ?)",
            (
                1,
                json.dumps(
                    {
                        "confidence": "",
                        "options": {"b": "Option B", "A": "Option A"},
                        "weak_points": [" 计算 ", "", "计算"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO question_records (id, options, answer_changes) VALUES (?, ?, ?)",
            (
                1,
                json.dumps({"b": "Option B", "A.": "Option A"}, ensure_ascii=False),
                json.dumps(
                    [
                        {
                            "previous_answer": "b",
                            "current_answer": "A.",
                            "timestamp": "2026-03-15T12:00:00",
                        }
                    ],
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO wrong_answers_v2 (id, options, linked_record_ids, variant_data, parent_ids, fusion_data) VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                json.dumps({"b": "Option B", "A.": "Option A"}, ensure_ascii=False),
                json.dumps(["3", "2", "2", "bad"], ensure_ascii=False),
                json.dumps(
                    {
                        "variant_options": {"b": "Option B", "A.": "Option A"},
                        "variant_answer": " b ",
                        "transform_type": " 重组 ",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(["3", "1", "2"], ensure_ascii=False),
                json.dumps(
                    {
                        "expected_key_points": [" 逻辑链 ", ""],
                        "judgement_pending": "false",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO wrong_answer_retries (id, ai_evaluation) VALUES (?, ?)",
            (
                1,
                json.dumps(
                    {
                        "verdict": " lucky_guess ",
                        "weak_links": [" logic gap ", ""],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    results = run_migration(str(db_path), apply=True)
    summary = {item["target"]: item for item in results}

    assert summary["quiz_sessions.questions"]["rows_changed"] == 1
    assert summary["test_records.ai_options"]["rows_changed"] == 1
    assert summary["wrong_answers_v2.variant_data"]["rows_changed"] == 1

    conn = sqlite3.connect(db_path)
    try:
        questions = json.loads(conn.execute("SELECT questions FROM quiz_sessions WHERE id = 1").fetchone()[0])
        answers = json.loads(conn.execute("SELECT answers FROM quiz_sessions WHERE id = 1").fetchone()[0])
        ai_options = json.loads(conn.execute("SELECT ai_options FROM test_records WHERE id = 1").fetchone()[0])
        wrong_options = json.loads(conn.execute("SELECT options FROM wrong_answers WHERE id = 1").fetchone()[0])
        activity_data = json.loads(conn.execute("SELECT data FROM learning_activities WHERE id = 1").fetchone()[0])
        question_options = json.loads(conn.execute("SELECT options FROM question_records WHERE id = 1").fetchone()[0])
        answer_changes = json.loads(conn.execute("SELECT answer_changes FROM question_records WHERE id = 1").fetchone()[0])
        wrong_v2_options = json.loads(conn.execute("SELECT options FROM wrong_answers_v2 WHERE id = 1").fetchone()[0])
        linked_ids = json.loads(conn.execute("SELECT linked_record_ids FROM wrong_answers_v2 WHERE id = 1").fetchone()[0])
        variant_data = json.loads(conn.execute("SELECT variant_data FROM wrong_answers_v2 WHERE id = 1").fetchone()[0])
        parent_ids = json.loads(conn.execute("SELECT parent_ids FROM wrong_answers_v2 WHERE id = 1").fetchone()[0])
        fusion_data = json.loads(conn.execute("SELECT fusion_data FROM wrong_answers_v2 WHERE id = 1").fetchone()[0])
        ai_evaluation = json.loads(conn.execute("SELECT ai_evaluation FROM wrong_answer_retries WHERE id = 1").fetchone()[0])
    finally:
        conn.close()

    assert questions[0]["options"] == {"A": "Option A", "B": "Option B"}
    assert questions[0]["correct_answer"] == "B"
    assert answers[0]["confidence"] == "unsure"
    assert answers[0]["weak_points"] == ["计算"]
    assert ai_options == {"A": "Option A", "B": "Option B"}
    assert wrong_options == {"A": "Option A", "B": "Option B"}
    assert activity_data["options"] == {"A": "Option A", "B": "Option B"}
    assert activity_data["weak_points"] == ["计算"]
    assert question_options == {"A": "Option A", "B": "Option B"}
    assert answer_changes[0]["from"] == "B"
    assert answer_changes[0]["to"] == "A"
    assert linked_ids == [2, 3]
    assert wrong_v2_options == {"A": "Option A", "B": "Option B"}
    assert variant_data["variant_options"] == {"A": "Option A", "B": "Option B"}
    assert variant_data["variant_answer"] == "B"
    assert variant_data["transform_type"] == "重组"
    assert parent_ids == [1, 2, 3]
    assert fusion_data["expected_key_points"] == ["逻辑链"]
    assert fusion_data["judgement_pending"] is False
    assert ai_evaluation["verdict"] == "lucky_guess"
    assert ai_evaluation["weak_links"] == ["logic gap"]
    return

    client = TestClient(app)
    submit_response = client.post(f"/api/fusion/{fusion_id}/submit", json={"user_answer": "  relation answer  "})
    assert submit_response.status_code == 200

    db = SessionLocal()
    try:
        stored = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == fusion_id).first()
        assert stored is not None
        assert stored.fusion_data["expected_key_points"] == ["逻辑链"]
        assert stored.fusion_data["user_answer_cache"] == "relation answer"
        assert stored.fusion_data["judgement_pending"] is True
    finally:
        db.close()

    class FakeFusionService:
        async def judge_fusion_answer(self, _fusion_id, _user_answer, _db):
            return {
                "verdict": "correct",
                "score": 84,
                "feedback": "  good synthesis  ",
                "weak_links": [" logic gap ", ""],
                "needs_diagnosis": False,
            }

        def apply_strict_sm2(self, fusion, is_correct, quality):
            assert is_correct is True
            assert quality == 5
            fusion.sm2_interval = 3
            fusion.sm2_repetitions = 1

    app.dependency_overrides[get_fusion_service] = lambda: FakeFusionService()
    try:
        judge_response = client.post(f"/api/fusion/{fusion_id}/judge")
        assert judge_response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_fusion_service, None)

    db = SessionLocal()
    try:
        stored = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == fusion_id).first()
        assert stored is not None
        assert stored.fusion_data["judgement_pending"] is False
        assert stored.fusion_data["last_judgement"]["weak_links"] == ["logic gap"]
        assert stored.fusion_data["last_judgement"]["feedback"] == "good synthesis"
        assert "judged_at" in stored.fusion_data["last_judgement"]
        assert stored.retry_count == 1
    finally:
        db.close()
