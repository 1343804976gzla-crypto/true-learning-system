from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.domains import ContentBase, CoreBase, ReviewBase, RuntimeBase
import services.chapter_review_service as chapter_review_service_module
from learning_tracking_models import ChapterReviewChapter, ChapterReviewTask, ChapterReviewTaskQuestion, ChapterReviewUnit
from main import app
from models import Base, Chapter, get_db


@pytest.fixture(autouse=True)
def disable_single_user_mode(monkeypatch):
    from services.data_identity import clear_identity_caches_for_tests

    monkeypatch.setenv("SINGLE_USER_MODE", "false")
    clear_identity_caches_for_tests()
    try:
        yield
    finally:
        monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
        clear_identity_caches_for_tests()


@pytest.fixture(autouse=True)
def stub_light_explanation_rewriter(monkeypatch):
    async def _passthrough(unit, summary, questions):
        return questions

    async def _skip_blueprint(**kwargs):
        raise TimeoutError("skip ai blueprint")

    monkeypatch.setattr(
        "services.chapter_review_service._ai_rewrite_question_explanations",
        _passthrough,
    )
    monkeypatch.setattr(
        "services.chapter_review_service._ai_refine_review_concept_blueprint",
        _skip_blueprint,
    )


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for metadata in (CoreBase.metadata, ContentBase.metadata, RuntimeBase.metadata, ReviewBase.metadata):
        metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield Session
    finally:
        for metadata in (ReviewBase.metadata, RuntimeBase.metadata, ContentBase.metadata, CoreBase.metadata):
            metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory):
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_upload_creates_review_plan_task_and_pdf_export(client, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            return {
                "book": "内科学",
                "edition": "1",
                "chapter_number": "2",
                "chapter_title": "心力衰竭",
                "chapter_id": "med_ch2_hf",
                "summary": "讲述心衰的定义、分型、诱因与处理要点。",
                "concepts": [
                    {"id": "hf_def", "name": "心衰定义"},
                    {"id": "hf_tx", "name": "治疗原则"},
                ],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"请概述心衰相关要点（角度{i}）。",
                "reference_answer": "应覆盖定义、诱因和处理原则。",
                "key_points": ["定义", "诱因", "处理原则"],
                "explanation": "答案应围绕原文中的关键概念组织。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_refine(unit, summary, questions):
        return questions

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)

    headers = {"x-tls-device-id": "review-device-a"}
    upload_response = client.post(
        "/api/upload",
        json={
            "content": "心力衰竭的定义。心力衰竭的诱因。心力衰竭的处理原则。\n\n急性失代偿时要尽快评估容量状态。",
            "date": "2026-03-19",
        },
        headers=headers,
    )
    assert upload_response.status_code == 200

    plan_response = client.get("/api/history/review-plan", headers=headers)
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["task_count"] >= 1

    task_id = plan_payload["tasks"][0]["task_id"]
    detail_response = client.get(f"/api/history/review-task/{task_id}", headers=headers)
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert len(detail_payload["questions"]) == 10
    assert detail_payload["chapter_title"] == "心力衰竭"
    assert detail_payload["source_content"]
    assert detail_payload["content_version"] == 1

    autosave_response = client.post(
        f"/api/history/review-task/{task_id}/autosave",
        json={
            "resume_position": 2,
            "answers": [
                {
                    "question_id": detail_payload["questions"][0]["id"],
                    "user_answer": "心衰需要先明确概念和诱因。",
                },
                {
                    "question_id": detail_payload["questions"][1]["id"],
                    "user_answer": "急性失代偿时要先评估容量状态。",
                },
            ],
        },
        headers=headers,
    )
    assert autosave_response.status_code == 200
    assert autosave_response.json()["answered_count"] == 2
    assert autosave_response.json()["resume_position"] == 2

    pdf_response = client.get("/api/history/review-pdf", headers=headers)
    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"].startswith("application/pdf")
    assert pdf_response.content[:4] == b"%PDF"


def test_review_task_grading_and_completion_flow(client, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            return {
                "book": "外科学",
                "edition": "1",
                "chapter_number": "5",
                "chapter_title": "休克",
                "chapter_id": "surgery_shock",
                "summary": "讲述休克的定义、分型和处理流程。",
                "concepts": [{"id": "shock", "name": "休克"}],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"休克的关键点{i}是什么？",
                "reference_answer": "需要说明定义、分型和处理思路。",
                "key_points": ["定义", "分型", "处理思路"],
                "explanation": "答案应覆盖原文中的三部分。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_grading(task):
        return {
            "results": [
                {
                    "position": question.position,
                    "score": 88,
                    "good_points": ["定义", "处理思路"],
                    "missing_points": ["分型"],
                    "feedback": "主要框架正确，但还缺一个关键点。",
                    "suggestion": "补上分型后会更完整。",
                }
                for question in task.questions
            ],
            "recommended_status": "normal",
            "overall_feedback": "整体作答扎实，可以进入下一轮复习。",
        }

    async def fake_ai_refine(unit, summary, questions):
        return questions

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)
    monkeypatch.setattr("services.chapter_review_service._ai_grade_questions", fake_ai_grading)

    headers = {"x-tls-device-id": "review-device-b"}
    upload_response = client.post(
        "/api/upload",
        json={
            "content": "休克的定义。休克的分型。休克的处理思路。",
            "date": "2026-03-19",
        },
        headers=headers,
    )
    assert upload_response.status_code == 200

    plan_payload = client.get("/api/history/review-plan", headers=headers).json()
    task_id = plan_payload["tasks"][0]["task_id"]
    detail_payload = client.get(f"/api/history/review-task/{task_id}", headers=headers).json()

    autosave_payload = {
        "resume_position": 10,
        "answers": [
            {
                "question_id": question["id"],
                "user_answer": "休克需要说明定义、分型和处理思路。",
            }
            for question in detail_payload["questions"]
        ],
    }
    autosave_response = client.post(
        f"/api/history/review-task/{task_id}/autosave",
        json=autosave_payload,
        headers=headers,
    )
    assert autosave_response.status_code == 200
    assert autosave_response.json()["answered_count"] == 10

    grade_response = client.post(f"/api/history/review-task/{task_id}/grade", headers=headers)
    assert grade_response.status_code == 200
    grade_payload = grade_response.json()
    assert grade_payload["ai_recommended_status"] == "normal"
    assert grade_payload["status"] == "awaiting_choice"
    assert grade_payload["grading_score"] == 88.0

    complete_response = client.post(
        f"/api/history/review-task/{task_id}/complete",
        json={"selected_status": "normal"},
        headers=headers,
    )
    assert complete_response.status_code == 200
    complete_payload = complete_response.json()
    assert complete_payload["status"] == "completed"
    assert complete_payload["user_selected_status"] == "normal"


def test_reupload_same_chapter_merges_content_and_resets_review_cycle(client, session_factory, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            if "补充" in content:
                summary = "更新后的总结，加入了新的处理策略。"
            else:
                summary = "初次上传的总结。"
            return {
                "book": "内科学",
                "edition": "1",
                "chapter_number": "2",
                "chapter_title": "心力衰竭",
                "chapter_id": "med_ch2_hf",
                "summary": summary,
                "concepts": [
                    {"id": "hf_def", "name": "心衰定义"},
                    {"id": "hf_tx", "name": "治疗原则"},
                ],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        prompt_bank = [
            (
                "请简述心力衰竭的定义，并指出答题时必须交代的判断要点。",
                "参考答案应先概括心脏泵血功能受损的本质，再说明由此导致组织灌注不足或淤血的核心后果，形成定义闭环。",
                ["泵血功能受损", "组织灌注不足", "淤血后果"],
                "本题核心是把概念写完整，而不是只写“心功能不好”。易错点是漏掉灌注不足或淤血后果，导致定义只剩一个残句。作答时宜按“本质-结果-判断”三步展开。",
            ),
            (
                "请概括心力衰竭的常见诱因，并说明作答时如何组织答案。",
                "作答时应围绕感染、心律失常、容量负荷增加等常见诱因展开，并强调这些因素会在原有心功能基础上诱发失代偿。",
                ["感染", "心律失常", "容量负荷增加"],
                "本题考查诱因归纳，不是机械罗列名词。常见失分点是只写诱因名称，不补充它们为何会诱发失代偿。答题时先分类列举，再点明与失代偿的关系更稳。",
            ),
            (
                "请说明急性失代偿性心力衰竭的处理策略，并指出答题抓手。",
                "高分答案应先写容量状态和氧合评估，再写利尿、减轻前后负荷及动态监测等处理主线，体现先稳定后调整的临床顺序。",
                ["评估容量状态", "利尿与减负荷", "动态监测"],
                "本题真正考查的是急性处理顺序。易错点是把检查、监测和治疗混成一团，没有体现先评估后干预的临床逻辑。作答时可按“评估-干预-复评”三步组织。",
            ),
        ]
        return [
            {
                "prompt": f"{prompt_bank[(i - 1) % len(prompt_bank)][0]}（版本{i}）",
                "reference_answer": prompt_bank[(i - 1) % len(prompt_bank)][1],
                "key_points": prompt_bank[(i - 1) % len(prompt_bank)][2],
                "explanation": prompt_bank[(i - 1) % len(prompt_bank)][3],
                "source_excerpt": unit.excerpt or unit.cleaned_text[:120],
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_refine(unit, summary, questions):
        return questions

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)

    headers = {"x-tls-device-id": "review-device-c"}
    first_content = "心力衰竭的定义。心力衰竭的诱因。"
    second_content = "心力衰竭的补充处理策略。补充随访与复盘要求。"

    first_upload = client.post(
        "/api/upload",
        json={
            "content": first_content,
            "date": "2026-03-18",
        },
        headers=headers,
    )
    assert first_upload.status_code == 200

    first_plan = client.get("/api/history/review-plan", headers=headers)
    assert first_plan.status_code == 200
    first_task_id = first_plan.json()["tasks"][0]["task_id"]

    first_detail = client.get(f"/api/history/review-task/{first_task_id}", headers=headers)
    assert first_detail.status_code == 200
    first_question_id = first_detail.json()["questions"][0]["id"]

    autosave = client.post(
        f"/api/history/review-task/{first_task_id}/autosave",
        json={
            "resume_position": 1,
            "answers": [
                {
                    "question_id": first_question_id,
                    "user_answer": "先回答定义与诱因。",
                }
            ],
        },
        headers=headers,
    )
    assert autosave.status_code == 200
    assert autosave.json()["status"] == "in_progress"

    second_upload = client.post(
        "/api/upload",
        json={
            "content": second_content,
            "date": "2026-03-19",
        },
        headers=headers,
    )
    assert second_upload.status_code == 200

    with session_factory() as db:
        review_chapter = (
            db.query(ChapterReviewChapter)
            .filter(ChapterReviewChapter.actor_key == "device:review-device-c")
            .first()
        )
        assert review_chapter is not None
        assert review_chapter.content_version == 2
        assert first_content in review_chapter.merged_raw_content
        assert second_content in review_chapter.merged_raw_content
        assert review_chapter.ai_summary == "更新后的总结，加入了新的处理策略。"
        assert review_chapter.next_due_date == date(2026, 3, 20)

        active_units = (
            db.query(ChapterReviewUnit)
            .filter(
                ChapterReviewUnit.review_chapter_id == review_chapter.id,
                ChapterReviewUnit.is_active.is_(True),
            )
            .all()
        )
        assert active_units
        assert all(unit.content_version == 2 for unit in active_units)
        assert all(unit.next_round == 1 for unit in active_units)

        cancelled_task = db.query(ChapterReviewTask).filter(ChapterReviewTask.id == first_task_id).first()
        assert cancelled_task is not None
        assert cancelled_task.status == "cancelled"

    second_plan = client.get("/api/history/review-plan", headers=headers)
    assert second_plan.status_code == 200
    second_plan_payload = second_plan.json()
    assert second_plan_payload["task_count"] >= 1
    assert second_plan_payload["tasks"][0]["task_id"] != first_task_id

    second_task_id = second_plan_payload["tasks"][0]["task_id"]
    second_detail = client.get(f"/api/history/review-task/{second_task_id}", headers=headers)
    assert second_detail.status_code == 200
    second_detail_payload = second_detail.json()
    assert second_detail_payload["content_version"] == 2
    assert first_content in second_detail_payload["source_content"]
    assert second_content in second_detail_payload["source_content"]


def test_existing_low_quality_questions_are_regenerated(client, session_factory, monkeypatch):
    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"请说明正反馈的核心机制，并概括其与稳态的关系（题{i}）。",
                "reference_answer": "正反馈会让受控变量沿原方向继续增强，短时间内放大生理过程，并用于完成特定目标性事件。",
                "key_points": ["受控变量沿原方向增强", "短时间内放大反应", "用于完成特定目标性事件"],
                "explanation": "本题考查正反馈的核心机制。作答时既要说明它会放大原有变化，也要指出它并不负责维持原稳态，而是服务于特定生理目标。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_refine(unit, summary, questions):
        return questions

    with session_factory() as db:
        chapter = ChapterReviewChapter(
            actor_key="device:review-device-d",
            chapter_id="physio_ch01",
            book="生理学",
            chapter_number="1",
            chapter_title="绪论",
            ai_summary="控制系统与反馈调节。",
            merged_raw_content="正反馈会使系统活动沿原方向持续增强。",
            cleaned_content="正反馈会使系统活动沿原方向持续增强。",
            content_version=1,
            first_uploaded_date=date(2026, 3, 20),
            last_uploaded_date=date(2026, 3, 20),
            next_due_date=date(2026, 3, 21),
            review_status="due",
        )
        db.add(chapter)
        db.flush()

        unit = ChapterReviewUnit(
            review_chapter_id=chapter.id,
            content_version=1,
            unit_index=1,
            unit_title="绪论 · 单元 1",
            raw_text="正反馈会使系统活动沿原方向持续增强，并服务于特定生理目标。",
            cleaned_text="正反馈会使系统活动沿原方向持续增强，并服务于特定生理目标。",
            excerpt="正反馈会使系统活动沿原方向持续增强，并服务于特定生理目标。",
            char_count=32,
            estimated_minutes=16,
            next_round=1,
            completed_rounds=0,
            next_due_date=date(2026, 3, 21),
            review_status="pending",
            carry_over_count=0,
            is_active=True,
        )
        db.add(unit)
        db.flush()

        task = ChapterReviewTask(
            actor_key="device:review-device-d",
            review_chapter_id=chapter.id,
            unit_id=unit.id,
            content_version=1,
            scheduled_for=date(2026, 3, 21),
            due_reason="第 1 轮到期复习",
            estimated_minutes=16,
            question_count=2,
            status="pending",
            source_label="第 1 轮到期复习",
        )
        db.add(task)
        db.flush()

        task.questions.append(
            ChapterReviewTaskQuestion(
                position=1,
                prompt="那么主要来看正反馈的生理机制是什么？请简要阐述。",
                reference_answer="系统的活动越来越强。",
                key_points=["系统的活动越来越强"],
                explanation="请结合原文关键事实作答。",
                source_excerpt="系统的活动越来越强。",
            )
        )
        task.questions.append(
            ChapterReviewTaskQuestion(
                position=2,
                prompt="请比较所以你看正反馈与相关概念的异同点。",
                reference_answer="正反馈打破原来的平衡状态。",
                key_points=["正反馈打破原来的平衡状态"],
                explanation="请结合原文关键事实作答。",
                source_excerpt="正反馈打破原来的平衡状态。",
            )
        )
        db.commit()

    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)

    response = client.get("/api/history/review-task/1", headers={"x-tls-device-id": "review-device-d"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["questions"]) == 2
    assert payload["questions"][0]["prompt"].startswith("请说明正反馈的核心机制")
    assert not payload["questions"][0]["prompt"].startswith("那么")


def test_concept_blueprint_generates_diverse_angles_without_mechanical_extension():
    blueprint = [
        {
            "concept_name": "正反馈",
            "prompt_focus": "正反馈",
            "question_axis": "mechanism",
            "source_excerpt": "正反馈会使受控部分活动沿原方向持续增强，并打破原有稳态。",
            "supporting_text": "正反馈会使受控部分活动沿原方向持续增强，并打破原有稳态。",
            "expected_key_points": ["沿原方向持续增强", "打破原有稳态"],
            "reference_answer": "正反馈使受控部分活动沿原方向增强，从而放大反应并推动过程达到顶峰。",
            "explanation_hint": "要从调节方向和结果理解正反馈。",
        },
        {
            "concept_name": "调定点",
            "prompt_focus": "调定点",
            "question_axis": "comparison",
            "source_excerpt": "负反馈朝着调定点工作，而正反馈背离调定点。",
            "supporting_text": "负反馈朝着调定点工作，而正反馈背离调定点。",
            "expected_key_points": ["负反馈朝着调定点", "正反馈背离调定点"],
            "reference_answer": "调定点是负反馈的工作目标，比较两类反馈时必须写清这一点。",
            "explanation_hint": "比较题要先写判断标准，再写差异。",
        },
        {
            "concept_name": "血液凝固中的正反馈",
            "prompt_focus": "正反馈的双重作用",
            "question_axis": "significance",
            "source_excerpt": "凝血中的正反馈有助于止血，但过强时可能导致血栓形成。",
            "supporting_text": "凝血中的正反馈有助于止血，但过强时可能导致血栓形成。",
            "expected_key_points": ["有助于止血", "过强可致血栓"],
            "reference_answer": "凝血过程体现了正反馈的双重作用，既有生理意义，也可能带来病理风险。",
            "explanation_hint": "要把生理获益和病理风险一起写出来。",
        },
    ]

    questions = chapter_review_service_module._questions_from_concept_blueprint(blueprint, question_count=7)

    assert len(questions) == 7
    assert len({item["prompt"] for item in questions}) == 7
    assert all("延展" not in item["prompt"] for item in questions)
    semantic_keys = {chapter_review_service_module._question_semantic_key(item) for item in questions}
    assert len(semantic_keys) >= 6


def test_light_rewriter_upgrades_question_explanations(client, monkeypatch):
    rewrite_calls = {"count": 0}

    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            return {
                "book": "生理学",
                "edition": "1",
                "chapter_number": "1",
                "chapter_title": "绪论",
                "chapter_id": "physio_ch01",
                "summary": "讲述正反馈、负反馈与稳态的关系。",
                "concepts": [
                    {"id": "pf", "name": "正反馈"},
                    {"id": "nf", "name": "负反馈"},
                    {"id": "steady", "name": "稳态"},
                ],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"请说明正反馈与稳态的关系（题{i}）。",
                "reference_answer": "正反馈使受控变量沿原方向继续增强，短时间内打破原稳态，用于完成特定目标性生理事件。",
                "key_points": ["沿原方向增强", "打破原稳态", "服务于特定生理目标"],
                "explanation": "请结合原文关键事实作答。",
                "source_excerpt": "正反馈绝对不能去维持原来系统的稳态，正反馈要打破这个稳态。",
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_refine(unit, summary, questions):
        return questions

    async def fake_ai_rewrite(unit, summary, questions):
        rewrite_calls["count"] += 1
        rewritten = []
        for item in questions:
            updated = dict(item)
            updated["explanation"] = (
                "本题真正考查的是正反馈的调节方向及其与稳态的关系。作答时先写受控变量沿原方向增强，"
                "再交代它为何暂时打破原稳态并服务于特定生理目标。易错点是把正反馈误写成维持稳态的机制，"
                "或只写“滚雪球”而不解释生理意义。答题时按“方向-结果-意义”展开更稳。"
            )
            rewritten.append(updated)
        return rewritten

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)
    monkeypatch.setattr("services.chapter_review_service._ai_rewrite_question_explanations", fake_ai_rewrite)
    monkeypatch.setattr(
        "services.chapter_review_service._supplement_question_candidates",
        lambda unit, *, questions, question_count, summary="", chapter_title="": questions,
    )
    monkeypatch.setattr(
        "services.chapter_review_service._question_batch_is_usable",
        lambda questions, *, question_count: True,
    )
    monkeypatch.setattr(
        "services.chapter_review_service._question_batch_is_serviceable",
        lambda questions, *, question_count: True,
    )

    headers = {"x-tls-device-id": "review-device-light"}
    upload_response = client.post(
        "/api/upload",
        json={
            "content": (
                "正反馈会让受控部分的活动朝着原先活动相同的方向改变，所以系统活动不断加强。"
                "正反馈绝对不能去维持原来系统的稳态，而是为了完成某些目标性生理事件。"
            ),
            "date": "2026-03-21",
        },
        headers=headers,
    )
    assert upload_response.status_code == 200

    task_id = client.get("/api/history/review-plan", headers=headers).json()["tasks"][0]["task_id"]
    detail_response = client.get(f"/api/history/review-task/{task_id}", headers=headers)
    assert detail_response.status_code == 200
    explanation = detail_response.json()["questions"][0]["explanation"]
    assert rewrite_calls["count"] >= 1
    assert "本题真正考查的是" in explanation
    assert ("易错点" in explanation) or ("失分点" in explanation)
    assert ("稳态" in explanation) or ("方向" in explanation)


def test_review_generation_anchors_questions_to_structured_concepts(client, session_factory, monkeypatch):
    async def fake_ai_question_generation(unit, summary, *, question_count):
        raise TimeoutError("skip direct question generation")

    async def fake_ai_refine(unit, summary, questions):
        raise TimeoutError("skip refine")

    async def fake_ai_blueprint(**kwargs):
        return [
            {
                "concept_name": "正反馈",
                "prompt_focus": "正反馈",
                "question_axis": "mechanism",
                "source_excerpt": "正反馈绝对不能去维持原来系统的稳态，正反馈要打破这个稳态。",
                "expected_key_points": ["沿原方向加强", "打破原稳态", "服务于特定生理目标"],
                "selection_reason": "这是本段最核心的调节概念",
                "priority": 10,
            },
            {
                "concept_name": "调定点",
                "prompt_focus": "调定点",
                "question_axis": "comparison",
                "source_excerpt": "负反馈才是以调定点作为它的目标的，负反馈朝着调定点。",
                "expected_key_points": ["负反馈以调定点为目标", "正反馈背离调定点"],
                "selection_reason": "容易与正反馈目标混淆",
                "priority": 9,
            },
        ]

    with session_factory() as db:
        db.add(
            Chapter(
                id="physio_ch01",
                book="生理学",
                edition="1",
                chapter_number="1",
                chapter_title="绪论",
                content_summary="讲述反馈调节、正反馈、负反馈和调定点。",
                concepts=[
                    {"id": "pf", "name": "正反馈"},
                    {"id": "nf", "name": "负反馈"},
                    {"id": "sp", "name": "调定点"},
                ],
                first_uploaded=date(2026, 3, 1),
            )
        )
        db.flush()

        chapter = ChapterReviewChapter(
            actor_key="device:review-device-f",
            chapter_id="physio_ch01",
            book="生理学",
            chapter_number="1",
            chapter_title="绪论",
            ai_summary="讲述反馈调节、正反馈和调定点。",
            merged_raw_content="正反馈让系统活动不断加强。正反馈绝对不能去维持原来系统的稳态。负反馈才是以调定点作为它的目标。",
            cleaned_content="正反馈让系统活动不断加强。正反馈绝对不能去维持原来系统的稳态。负反馈才是以调定点作为它的目标。",
            content_version=1,
            first_uploaded_date=date(2026, 3, 20),
            last_uploaded_date=date(2026, 3, 20),
            next_due_date=date(2026, 3, 21),
            review_status="due",
        )
        db.add(chapter)
        db.flush()

        unit = ChapterReviewUnit(
            review_chapter_id=chapter.id,
            content_version=1,
            unit_index=1,
            unit_title="绪论 · 单元 1",
            raw_text=(
                "最终，就让受控部分的活动朝着与他原先活动相同的方向改变了，所以他是一直加强。"
                "正反馈绝对不能去维持原来系统的稳态。负反馈才是以调定点作为它的目标。"
            ),
            cleaned_text=(
                "最终，就让受控部分的活动朝着与他原先活动相同的方向改变了，所以他是一直加强。"
                "正反馈绝对不能去维持原来系统的稳态。负反馈才是以调定点作为它的目标。"
            ),
            excerpt="正反馈绝对不能去维持原来系统的稳态。负反馈才是以调定点作为它的目标。",
            char_count=80,
            estimated_minutes=16,
            next_round=1,
            completed_rounds=0,
            next_due_date=date(2026, 3, 21),
            review_status="pending",
            carry_over_count=0,
            is_active=True,
        )
        db.add(unit)
        db.flush()

        task = ChapterReviewTask(
            actor_key="device:review-device-f",
            review_chapter_id=chapter.id,
            unit_id=unit.id,
            content_version=1,
            scheduled_for=date(2026, 3, 21),
            due_reason="第 1 轮到期复习",
            estimated_minutes=16,
            question_count=4,
            status="pending",
            source_label="第 1 轮到期复习",
        )
        db.add(task)
        db.commit()

    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_question_generation)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_review_concept_blueprint", fake_ai_blueprint)

    response = client.get("/api/history/review-task/1", headers={"x-tls-device-id": "review-device-f"})
    assert response.status_code == 200
    payload = response.json()
    prompts = [item["prompt"] for item in payload["questions"]]
    assert any("正反馈" in prompt for prompt in prompts)
    assert any("调定点" in prompt for prompt in prompts)
    assert all("滚雪球" not in prompt for prompt in prompts)
    assert all(not prompt.startswith("那么") for prompt in prompts)


def test_review_sync_prefers_content_matching_chapter_when_parser_misclassifies(client, session_factory, monkeypatch):
    class FakeParser:
        async def parse_content_with_knowledge(self, content, db):
            return {
                "book": "内科学",
                "edition": "1",
                "chapter_number": "7",
                "chapter_title": "呼吸衰竭与肺癌",
                "chapter_id": "internal_ch07",
                "summary": "围绕消化液、胰液和胆汁的作用展开。",
                "concepts": [{"id": "digestive", "name": "消化液"}],
            }

    async def fake_ai_questions(unit, summary, *, question_count):
        return [
            {
                "prompt": f"第{i}题：请概括胰液与胆汁的作用。",
                "reference_answer": "胰液负责多种营养物质消化，胆汁帮助脂肪消化吸收。",
                "key_points": ["胰液消化多种营养物质", "胆汁帮助脂肪消化吸收"],
                "explanation": "答案需要分别说明胰液和胆汁的作用分工，并点出脂肪消化吸收这一重点。",
                "source_excerpt": unit.excerpt or unit.cleaned_text[:80],
            }
            for i in range(1, question_count + 1)
        ]

    async def fake_ai_refine(unit, summary, questions):
        return questions

    with session_factory() as db:
        db.add(
            Chapter(
                id="internal_ch07",
                book="内科学",
                edition="1",
                chapter_number="7",
                chapter_title="呼吸衰竭与肺癌",
                content_summary="讲述呼吸衰竭、肺癌与鉴别诊断。",
                concepts=[{"id": "resp", "name": "呼吸衰竭"}, {"id": "cancer", "name": "肺癌"}],
                first_uploaded=date(2026, 3, 1),
            )
        )
        db.add(
            Chapter(
                id="physio_ch16",
                book="生理学",
                edition="1",
                chapter_number="16",
                chapter_title="口腔食管和胃内消化",
                content_summary="讲述胃液、胰液、胆汁、小肠内消化与吸收。",
                concepts=[{"id": "pancreatic", "name": "胰液"}, {"id": "bile", "name": "胆汁"}],
                first_uploaded=date(2026, 3, 1),
            )
        )
        db.commit()

    monkeypatch.setattr("routers.upload.get_content_parser", lambda: FakeParser())
    monkeypatch.setattr("services.chapter_review_service._ai_generate_questions", fake_ai_questions)
    monkeypatch.setattr("services.chapter_review_service._ai_refine_questions", fake_ai_refine)

    headers = {"x-tls-device-id": "review-device-e"}
    upload_response = client.post(
        "/api/upload",
        json={
            "content": "我们今天来学习肠道里面的消化。小肠里面重点介绍胰液和胆汁，它们共同影响脂肪的消化吸收。",
            "date": "2026-03-20",
        },
        headers=headers,
    )
    assert upload_response.status_code == 200

    with session_factory() as db:
        review_chapter = (
            db.query(ChapterReviewChapter)
            .filter(ChapterReviewChapter.actor_key == "device:review-device-e")
            .first()
        )
        assert review_chapter is not None
        assert review_chapter.chapter_id == "physio_ch16"
        assert review_chapter.chapter_title == "口腔食管和胃内消化"
