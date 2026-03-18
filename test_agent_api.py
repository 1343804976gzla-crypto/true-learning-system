from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from main import app


class _FakeAIClient:
    async def generate_content(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
        use_heavy: bool,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ) -> str:
        assert "学习数据" in prompt
        assert "本轮任务拆解" in prompt
        assert "候选执行计划" in prompt
        assert "[当前回答策略]" in prompt
        assert preferred_provider == "deepseek"
        assert preferred_model == "deepseek-chat"
        return "这是一个基于学习数据生成的测试回答。"

    async def generate_content_stream(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
        use_heavy: bool,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ):
        assert "学习数据" in prompt
        assert "本轮任务拆解" in prompt
        assert "候选执行计划" in prompt
        assert "[当前回答策略]" in prompt
        assert preferred_provider == "deepseek"
        assert preferred_model == "deepseek-chat"
        for chunk in ["这是一个", "基于学习数据", "生成的测试回答。"]:
            yield chunk


def _seed_agent_learning_data(device_id: str) -> None:
    from learning_tracking_models import LearningSession, QuestionRecord, WrongAnswerRetry, WrongAnswerV2
    from models import Chapter, ConceptMastery, DailyUpload, SessionLocal, TestRecord
    from services.data_identity import ensure_learning_identity_schema

    ensure_learning_identity_schema()

    now = datetime.now()
    today = date.today()
    chapter_id = f"seed-chapter-{uuid4().hex}"
    concept_id = f"seed-concept-{uuid4().hex}"
    session_id = f"seed-session-{uuid4().hex}"

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=chapter_id,
                book="Seed Book",
                edition="1",
                chapter_number="1",
                chapter_title="Seed Chapter",
                concepts=[],
                first_uploaded=today,
            )
        )
        db.add_all(
            [
                DailyUpload(
                    device_id=device_id,
                    date=today,
                    raw_content="seed upload",
                    ai_extracted={
                        "book": "Seed Book",
                        "chapter_title": "Seed Chapter",
                        "chapter_id": chapter_id,
                        "summary": "seed summary",
                    },
                ),
                ConceptMastery(
                    concept_id=concept_id,
                    device_id=device_id,
                    chapter_id=chapter_id,
                    name="Seed Concept",
                    retention=0.35,
                    understanding=0.4,
                    application=0.3,
                    next_review=today,
                ),
                LearningSession(
                    id=session_id,
                    device_id=device_id,
                    session_type="exam",
                    chapter_id=chapter_id,
                    title="Seed Session",
                    status="completed",
                    total_questions=2,
                    correct_count=1,
                    wrong_count=1,
                    score=50,
                    accuracy=0.5,
                    started_at=now - timedelta(hours=1),
                    completed_at=now - timedelta(minutes=30),
                    duration_seconds=1800,
                ),
                QuestionRecord(
                    session_id=session_id,
                    device_id=device_id,
                    question_index=0,
                    question_type="A1",
                    difficulty="基础",
                    question_text="Seed Question 1",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    key_point="Seed Concept",
                    answered_at=now - timedelta(hours=1),
                    time_spent_seconds=30,
                ),
                QuestionRecord(
                    session_id=session_id,
                    device_id=device_id,
                    question_index=1,
                    question_type="A2",
                    difficulty="提高",
                    question_text="Seed Question 2",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="B",
                    user_answer="C",
                    is_correct=False,
                    confidence="unsure",
                    key_point="Seed Concept",
                    answered_at=now - timedelta(minutes=50),
                    time_spent_seconds=45,
                ),
                TestRecord(
                    device_id=device_id,
                    concept_id=concept_id,
                    test_type="ai_quiz",
                    ai_question="Seed AI question",
                    ai_options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    ai_correct_answer="A",
                    user_answer="A",
                    confidence="sure",
                    is_correct=True,
                    score=100,
                    tested_at=now - timedelta(minutes=20),
                ),
            ]
        )
        db.commit()

        wrong_answer = WrongAnswerV2(
            device_id=device_id,
            question_fingerprint=f"seed-fp-{uuid4().hex}",
            question_text="Seed Wrong Question",
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            correct_answer="A",
            explanation="seed explanation",
            key_point="Seed Concept",
            question_type="A2",
            difficulty="提高",
            chapter_id=chapter_id,
            error_count=1,
            encounter_count=1,
            severity_tag="critical",
            mastery_status="active",
            next_review_date=today,
            first_wrong_at=now - timedelta(minutes=40),
            last_wrong_at=now - timedelta(minutes=40),
        )
        db.add(wrong_answer)
        db.commit()
        db.refresh(wrong_answer)

        db.add(
            WrongAnswerRetry(
                device_id=device_id,
                wrong_answer_id=int(wrong_answer.id),
                user_answer="A",
                is_correct=True,
                confidence="sure",
                retried_at=now - timedelta(minutes=10),
            )
        )
        db.commit()


def _seed_scoped_wrong_answer(
    *,
    user_id: str | None = None,
    device_id: str | None = None,
    question_text: str,
    key_point: str,
    next_review_offset_days: int = 0,
) -> int:
    from learning_tracking_models import WrongAnswerV2
    from models import SessionLocal
    from services.data_identity import ensure_learning_identity_schema

    ensure_learning_identity_schema()

    now = datetime.now()
    with SessionLocal() as db:
        wrong_answer = WrongAnswerV2(
            user_id=user_id,
            device_id=device_id,
            question_fingerprint=f"scoped-fp-{uuid4().hex}",
            question_text=question_text,
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            correct_answer="A",
            explanation="scoped explanation",
            key_point=key_point,
            question_type="A1",
            difficulty="基础",
            error_count=1,
            encounter_count=1,
            severity_tag="critical",
            mastery_status="active",
            next_review_date=date.today() + timedelta(days=next_review_offset_days),
            first_wrong_at=now - timedelta(minutes=20),
            last_wrong_at=now - timedelta(minutes=20),
        )
        db.add(wrong_answer)
        db.commit()
        db.refresh(wrong_answer)
        return int(wrong_answer.id)


def _create_agent_action_session(
    client: TestClient,
    *,
    title: str,
    device_id: str | None = None,
    user_id: str | None = None,
) -> str:
    payload = {
        "title": title,
        "agent_type": "tutor",
    }
    if device_id:
        payload["device_id"] = device_id
    if user_id:
        payload["user_id"] = user_id

    response = client.post("/api/agent/sessions", json=payload)
    assert response.status_code == 200
    return response.json()["id"]


def _preview_agent_action(
    client: TestClient,
    *,
    session_id: str,
    tool_name: str,
    tool_args: dict,
    task_id: str | None = None,
    device_id: str | None = None,
    user_id: str | None = None,
):
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
    }
    if task_id:
        payload["task_id"] = task_id
    if device_id:
        payload["device_id"] = device_id
    if user_id:
        payload["user_id"] = user_id
    return client.post("/api/agent/actions", json=payload)


def _confirm_agent_action(
    client: TestClient,
    *,
    session_id: str,
    action_id: str,
    device_id: str | None = None,
    user_id: str | None = None,
):
    payload = {
        "session_id": session_id,
        "action_id": action_id,
        "confirm": True,
    }
    if device_id:
        payload["device_id"] = device_id
    if user_id:
        payload["user_id"] = user_id
    return client.post("/api/agent/actions", json=payload)


def _rollback_agent_action_request(
    client: TestClient,
    *,
    session_id: str,
    action_id: str,
    device_id: str | None = None,
    user_id: str | None = None,
):
    payload = {
        "session_id": session_id,
        "action_id": action_id,
        "rollback": True,
    }
    if device_id:
        payload["device_id"] = device_id
    if user_id:
        payload["user_id"] = user_id
    return client.post("/api/agent/actions", json=payload)


def _create_agent_task_request(
    client: TestClient,
    *,
    session_id: str,
    plan_bundle: dict,
    device_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
    goal: str | None = None,
    initial_status: str = "ready",
):
    payload = {
        "session_id": session_id,
        "plan_bundle": plan_bundle,
        "initial_status": initial_status,
    }
    if device_id:
        payload["device_id"] = device_id
    if user_id:
        payload["user_id"] = user_id
    if title:
        payload["title"] = title
    if goal:
        payload["goal"] = goal
    return client.post("/api/agent/tasks", json=payload)


def _update_agent_task_status_request(
    client: TestClient,
    *,
    task_id: str,
    status: str,
    device_id: str | None = None,
    user_id: str | None = None,
    note: str | None = None,
):
    payload = {"status": status}
    if device_id:
        payload["device_id"] = device_id
    if user_id:
        payload["user_id"] = user_id
    if note:
        payload["note"] = note
    return client.post(f"/api/agent/tasks/{task_id}/status", json=payload)


def test_agent_session_routes_work():
    client = TestClient(app)
    device_id = f"agent-test-{uuid4().hex}"

    create_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Agent API test session",
            "agent_type": "tutor",
        },
    )
    assert create_response.status_code == 200
    session = create_response.json()
    assert session["device_id"] == device_id
    assert session["message_count"] == 0
    assert session["provider"] == "deepseek"
    assert session["model"] == "deepseek-chat"

    list_response = client.get("/api/agent/sessions", params={"device_id": device_id, "status": "active"})
    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] >= 1
    assert any(item["id"] == session["id"] for item in payload["sessions"])

    messages_response = client.get(
        f"/api/agent/sessions/{session['id']}/messages",
        params={"device_id": device_id},
    )
    assert messages_response.status_code == 200
    assert messages_response.json()["total"] == 0


def test_agent_session_routes_require_identity_and_isolate_devices():
    client = TestClient(app)
    owner_device_id = f"agent-owner-{uuid4().hex}"
    other_device_id = f"agent-other-{uuid4().hex}"

    create_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": owner_device_id,
            "title": "Isolated session",
            "agent_type": "tutor",
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]

    list_response = client.get("/api/agent/sessions", params={"status": "active"})
    assert list_response.status_code == 400

    owner_detail_response = client.get(
        f"/api/agent/sessions/{session_id}",
        params={"device_id": owner_device_id},
    )
    assert owner_detail_response.status_code == 200

    detail_response = client.get(
        f"/api/agent/sessions/{session_id}",
        params={"device_id": other_device_id},
    )
    assert detail_response.status_code == 404

    messages_response = client.get(
        f"/api/agent/sessions/{session_id}/messages",
        params={"device_id": other_device_id},
    )
    assert messages_response.status_code == 404

    turns_response = client.get(
        f"/api/agent/sessions/{session_id}/turns",
        params={"device_id": other_device_id},
    )
    assert turns_response.status_code == 404

    summary_response = client.post(
        f"/api/agent/sessions/{session_id}/summarize",
        params={"device_id": other_device_id},
    )
    assert summary_response.status_code == 404

    hijack_response = client.post(
        "/api/agent/chat",
        json={
            "session_id": session_id,
            "device_id": other_device_id,
            "message": "Can I read another device session?",
            "agent_type": "tutor",
        },
    )
    assert hijack_response.status_code == 404


def test_agent_task_create_list_and_detail():
    client = TestClient(app)
    device_id = f"agent-task-{uuid4().hex}"
    session_id = _create_agent_action_session(
        client,
        title="Task session",
        device_id=device_id,
    )
    plan_bundle = {
        "summary": "今晚先排出高风险错题，再决定是否生成巩固题组",
        "tasks": [
            {
                "id": "focus-wrong",
                "title": "筛出高风险错题",
                "description": "先把今天最容易再错的题挑出来",
                "status": "pending",
                "priority": "high",
                "subtasks": [
                    {
                        "id": "focus-wrong-1",
                        "title": "抓 due 错题",
                        "description": "优先 due 项",
                        "status": "completed",
                        "priority": "high",
                        "tools": ["get_wrong_answers"],
                    }
                ],
            }
        ],
    }

    create_response = _create_agent_task_request(
        client,
        session_id=session_id,
        device_id=device_id,
        title="今晚复习推进任务",
        goal="把今晚的复习顺序和后续动作定下来",
        plan_bundle=plan_bundle,
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    task = payload["task"]

    assert task["title"] == "今晚复习推进任务"
    assert task["goal"] == "把今晚的复习顺序和后续动作定下来"
    assert task["status"] == "ready"
    assert task["task_count"] == 1
    assert task["subtask_count"] == 1
    assert task["completed_subtask_count"] == 1
    assert payload["events"][0]["event_type"] == "created"

    list_response = client.get(
        f"/api/agent/sessions/{session_id}/tasks",
        params={"device_id": device_id},
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] >= 1
    assert list_payload["tasks"][0]["id"] == task["id"]

    detail_response = client.get(
        f"/api/agent/tasks/{task['id']}",
        params={"device_id": device_id},
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["task"]["plan_summary"] == plan_bundle["summary"]
    assert detail_payload["events"][0]["to_status"] == "ready"


def test_agent_task_detail_and_status_require_identity():
    client = TestClient(app)
    device_id = f"agent-task-identity-{uuid4().hex}"
    session_id = _create_agent_action_session(
        client,
        title="Task identity session",
        device_id=device_id,
    )

    create_response = _create_agent_task_request(
        client,
        session_id=session_id,
        device_id=device_id,
        title="Identity locked task",
        plan_bundle={"summary": "identity required", "tasks": []},
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["task"]["id"]

    detail_response = client.get(f"/api/agent/tasks/{task_id}")
    assert detail_response.status_code == 400

    status_response = client.post(
        f"/api/agent/tasks/{task_id}/status",
        json={"status": "running"},
    )
    assert status_response.status_code == 400


def test_agent_task_status_transitions_and_event_log():
    client = TestClient(app)
    device_id = f"agent-task-status-{uuid4().hex}"
    session_id = _create_agent_action_session(
        client,
        title="Task transition session",
        device_id=device_id,
    )

    create_response = _create_agent_task_request(
        client,
        session_id=session_id,
        device_id=device_id,
        title="Task status flow",
        plan_bundle={"summary": "status flow", "tasks": []},
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["task"]["id"]

    running_response = _update_agent_task_status_request(
        client,
        task_id=task_id,
        device_id=device_id,
        status="running",
        note="开始执行",
    )
    assert running_response.status_code == 200
    running_payload = running_response.json()
    assert running_payload["task"]["status"] == "running"
    assert running_payload["task"]["started_at"] is not None
    assert running_payload["events"][-1]["to_status"] == "running"

    verifying_response = _update_agent_task_status_request(
        client,
        task_id=task_id,
        device_id=device_id,
        status="verifying",
    )
    assert verifying_response.status_code == 200
    assert verifying_response.json()["task"]["status"] == "verifying"

    completed_response = _update_agent_task_status_request(
        client,
        task_id=task_id,
        device_id=device_id,
        status="completed",
    )
    assert completed_response.status_code == 200
    completed_payload = completed_response.json()
    assert completed_payload["task"]["status"] == "completed"
    assert completed_payload["task"]["completed_at"] is not None
    assert completed_payload["task"]["available_transitions"] == []
    assert [item["event_type"] for item in completed_payload["events"]] == [
        "created",
        "status_changed",
        "status_changed",
        "status_changed",
    ]


def test_agent_task_routes_isolate_devices_and_reject_invalid_transition():
    client = TestClient(app)
    owner_device_id = f"agent-task-owner-{uuid4().hex}"
    other_device_id = f"agent-task-other-{uuid4().hex}"
    session_id = _create_agent_action_session(
        client,
        title="Task isolation session",
        device_id=owner_device_id,
    )

    create_response = _create_agent_task_request(
        client,
        session_id=session_id,
        device_id=owner_device_id,
        title="Owner only task",
        plan_bundle={"summary": "owner plan", "tasks": []},
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["task"]["id"]

    foreign_list_response = client.get(
        f"/api/agent/sessions/{session_id}/tasks",
        params={"device_id": other_device_id},
    )
    assert foreign_list_response.status_code == 404

    foreign_detail_response = client.get(
        f"/api/agent/tasks/{task_id}",
        params={"device_id": other_device_id},
    )
    assert foreign_detail_response.status_code == 404

    invalid_transition_response = _update_agent_task_status_request(
        client,
        task_id=task_id,
        device_id=owner_device_id,
        status="completed",
    )
    assert invalid_transition_response.status_code == 400


def test_agent_chat_auto_creates_task_when_action_suggestions_exist(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    client = TestClient(app)
    device_id = f"agent-auto-task-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "结合我的错题和知识点掌握情况，给我一份可以直接执行的复习建议。",
            "agent_type": "tutor",
            "requested_tools": ["get_wrong_answers", "get_knowledge_mastery"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    assert structured["action_suggestions"]

    session_id = payload["session"]["id"]
    tasks_response = client.get(
        f"/api/agent/sessions/{session_id}/tasks",
        params={"device_id": device_id},
    )
    assert tasks_response.status_code == 200
    tasks_payload = tasks_response.json()
    assert tasks_payload["total"] >= 1

    related_task = next(
        item for item in tasks_payload["tasks"] if item["related_turn_state_id"] == structured["turn_state_id"]
    )
    assert related_task["status"] == "ready"
    assert related_task["source"] == "plan"
    assert related_task["action_suggestions"]
    assert related_task["suggested_action_count"] == len(related_task["action_suggestions"])
    assert related_task["pending_action_count"] == len(related_task["action_suggestions"])
    assert related_task["plan_summary"]


def test_agent_action_syncs_task_preview_confirm_and_rollback():
    from learning_tracking_models import WrongAnswerV2
    from models import SessionLocal

    client = TestClient(app)
    device_id = f"agent-task-action-sync-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_id).first()
        assert wrong_answer is not None
        wrong_answer_id = int(wrong_answer.id)

    session_id = _create_agent_action_session(
        client,
        title="Task action sync session",
        device_id=device_id,
    )
    create_task_response = _create_agent_task_request(
        client,
        session_id=session_id,
        device_id=device_id,
        title="归档已通过错题",
        plan_bundle={"summary": "归档任务", "tasks": []},
    )
    assert create_task_response.status_code == 200
    task_id = create_task_response.json()["task"]["id"]

    preview_response = _preview_agent_action(
        client,
        session_id=session_id,
        task_id=task_id,
        device_id=device_id,
        tool_name="update_wrong_answer_status",
        tool_args={
            "wrong_answer_ids": [wrong_answer_id],
            "target_status": "archived",
            "reason": "task sync test",
        },
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["action"]["related_task_id"] == task_id

    task_detail_after_preview = client.get(
        f"/api/agent/tasks/{task_id}",
        params={"device_id": device_id},
    )
    assert task_detail_after_preview.status_code == 200
    preview_detail_payload = task_detail_after_preview.json()
    assert preview_detail_payload["task"]["status"] == "ready"
    assert any(item["event_type"] == "action_previewed" for item in preview_detail_payload["events"])
    assert preview_detail_payload["linked_actions"][0]["id"] == preview_payload["action"]["id"]
    preview_suggestion = preview_detail_payload["task"]["action_suggestions"][0]
    assert preview_suggestion["related_action_id"] == preview_payload["action"]["id"]
    assert preview_suggestion["approval_status"] == "pending"
    assert preview_suggestion["execution_status"] == "pending"
    assert preview_detail_payload["task"]["previewed_action_count"] == 1

    confirm_response = _confirm_agent_action(
        client,
        session_id=session_id,
        action_id=preview_payload["action"]["id"],
        device_id=device_id,
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["action"]["related_task_id"] == task_id

    task_detail_after_confirm = client.get(
        f"/api/agent/tasks/{task_id}",
        params={"device_id": device_id},
    )
    assert task_detail_after_confirm.status_code == 200
    confirm_detail_payload = task_detail_after_confirm.json()
    assert confirm_detail_payload["task"]["status"] == "running"
    assert any(item["event_type"] == "action_executed" for item in confirm_detail_payload["events"])
    confirm_suggestion = confirm_detail_payload["task"]["action_suggestions"][0]
    assert confirm_suggestion["execution_status"] == "success"
    assert confirm_suggestion["verification_status"] == "verified"
    assert confirm_detail_payload["task"]["completed_action_count"] == 1
    assert confirm_detail_payload["task"]["previewed_action_count"] == 0

    rollback_response = _rollback_agent_action_request(
        client,
        session_id=session_id,
        action_id=preview_payload["action"]["id"],
        device_id=device_id,
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["action"]["execution_status"] == "rolled_back"
    assert rollback_payload["action"]["related_task_id"] == task_id

    task_detail_after_rollback = client.get(
        f"/api/agent/tasks/{task_id}",
        params={"device_id": device_id},
    )
    assert task_detail_after_rollback.status_code == 200
    rollback_detail_payload = task_detail_after_rollback.json()
    assert any(item["event_type"] == "action_rolled_back" for item in rollback_detail_payload["events"])
    rollback_suggestion = rollback_detail_payload["task"]["action_suggestions"][0]
    assert rollback_suggestion["execution_status"] == "rolled_back"
    assert rollback_detail_payload["task"]["rolled_back_action_count"] == 1

def test_agent_chat_persists_messages_and_tool_calls(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    client = TestClient(app)
    device_id = f"agent-chat-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "请根据我的错题和进度，告诉我现在该怎么复习。",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"]["content"] == "这是一个基于学习数据生成的测试回答。"
    assert payload["assistant_message"]["message_status"] == "completed"
    assert payload["session"]["message_count"] >= 2
    assert payload["context_usage"]["total_estimated_tokens"] > 0
    assert len(payload["tool_calls"]) >= 1
    assert any(call["tool_name"] == "get_progress_summary" for call in payload["tool_calls"])
    assert any(call["tool_name"] == "get_review_pressure" for call in payload["tool_calls"])
    assert any(call["tool_name"] == "get_knowledge_mastery" for call in payload["tool_calls"])
    assert "request_analysis" in payload["assistant_message"]["content_structured"]
    assert payload["assistant_message"]["content_structured"]["request_analysis"]["focuses"]
    assert payload["assistant_message"]["content_structured"]["plan"]["tasks"][0]["title"] == "锁定本轮诉求"
    assert payload["assistant_message"]["content_structured"]["sources"]
    assert payload["assistant_message"]["content_structured"]["sources"][0]["title"]
    assert payload["assistant_message"]["content_structured"]["plan"]["tasks"]
    assert payload["assistant_message"]["content_structured"]["plan"]["summary"]
    assert payload["assistant_message"]["content_structured"]["response_strategy"]["strategy"]

    session_id = payload["session"]["id"]
    history_response = client.get(
        f"/api/agent/sessions/{session_id}/messages",
        params={"device_id": device_id},
    )
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["total"] >= 2
    assert history["messages"][-1]["role"] == "assistant"
    assert history["messages"][-1]["content_structured"]["sources"]
    assert history["messages"][-1]["content_structured"]["plan"]["tasks"]


def test_agent_chat_extracts_long_term_memories(monkeypatch):
    from agent_models import AgentMemory, AgentSession
    from models import SessionLocal
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    client = TestClient(app)
    device_id = f"agent-memory-store-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "这周我想优先复习心血管，今晚只能学30分钟。",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200

    with SessionLocal() as db:
        memories = (
            db.query(AgentMemory)
            .join(AgentSession, AgentSession.id == AgentMemory.session_id)
            .filter(
                AgentSession.device_id == device_id,
                AgentMemory.memory_type != "session_summary",
            )
            .order_by(AgentMemory.created_at, AgentMemory.id)
            .all()
        )

    memory_types = [item.memory_type for item in memories]
    summaries = [item.summary for item in memories]

    assert "user_goal" in memory_types
    assert "study_constraint" in memory_types
    assert any("优先复习心血管" in item for item in summaries)
    assert any("30分钟" in item for item in summaries)


def test_agent_chat_retrieves_long_term_memories_across_sessions(monkeypatch):
    from services import agent_runtime

    class _MemoryAwareFakeAIClient(_FakeAIClient):
        def __init__(self):
            self.calls = 0

        async def generate_content(
            self,
            prompt: str,
            max_tokens: int,
            temperature: float,
            timeout: int,
            use_heavy: bool,
            preferred_provider: str | None = None,
            preferred_model: str | None = None,
        ) -> str:
            self.calls += 1
            if self.calls == 2:
                assert "[Long-Term Memory]" in prompt
                assert "优先复习心血管" in prompt
                assert "30分钟" in prompt
            return await super().generate_content(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                use_heavy=use_heavy,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
            )

    memory_client = _MemoryAwareFakeAIClient()
    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: memory_client)

    client = TestClient(app)
    device_id = f"agent-memory-query-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    first = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "这周我想优先复习心血管，今晚只能学30分钟。",
            "agent_type": "tutor",
        },
    )
    assert first.status_code == 200
    first_session_id = first.json()["session"]["id"]

    second = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "如果我今天只有30分钟，心血管该怎么安排？",
            "agent_type": "tutor",
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    second_session_id = second_payload["session"]["id"]
    memory_hits = second_payload["assistant_message"]["content_structured"]["memories"]

    assert second_session_id != first_session_id
    assert second_payload["context_usage"]["memory_tokens"] > 0
    assert any("心血管" in item["summary"] for item in memory_hits)
    assert any("30分钟" in item["summary"] for item in memory_hits)


def test_agent_chat_uses_history_data_when_user_asks_for_history(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    client = TestClient(app)
    device_id = f"agent-history-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "结合我的上传历史、连续学习天数和最近学习轨迹，帮我复盘一下。",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    assert "get_study_history" in structured["selected_tools"]
    assert "get_learning_sessions" in structured["selected_tools"]
    assert any(focus["id"] == "history_reconstruction" for focus in structured["request_analysis"]["focuses"])


def test_agent_chat_persists_turn_states_and_reuses_tool_cache(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    call_counter = {"count": 0}

    async def _fake_execute_agent_tool(tool_name, db, overrides=None, user_id=None, device_id=None):
        call_counter["count"] += 1
        assert tool_name == "get_study_history"
        return (
            overrides or {},
            {
                "days": 30,
                "generated_at": "2026-03-15T00:00:00",
                "total_uploads_in_window": 4,
                "weekly_uploads": 2,
                "streak_days": 3,
                "book_distribution": {"内科学": 2, "病理学": 1},
                "recent_uploads": [
                    {
                        "id": 1,
                        "date": "2026-03-15",
                        "book": "内科学",
                        "chapter_title": "心力衰竭",
                        "chapter_id": "med-1",
                        "main_topic": "循环系统",
                        "summary": "测试摘要",
                    }
                ],
            },
            5,
        )

    monkeypatch.setattr(agent_runtime, "execute_agent_tool", _fake_execute_agent_tool)

    client = TestClient(app)
    device_id = f"agent-turn-cache-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    first = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "结合我的上传历史帮我复盘。",
            "agent_type": "tutor",
            "requested_tools": ["get_study_history"],
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    session_id = first_payload["session"]["id"]
    assert call_counter["count"] == 1
    assert first_payload["assistant_message"]["content_structured"]["execution_state"]["stats"]["cache_hits"] == 0

    second = client.post(
        "/api/agent/chat",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "message": "再结合我的上传历史帮我复盘一次。",
            "agent_type": "tutor",
            "requested_tools": ["get_study_history"],
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert call_counter["count"] == 1
    assert second_payload["assistant_message"]["content_structured"]["execution_state"]["stats"]["cache_hits"] >= 1

    turns_response = client.get(
        f"/api/agent/sessions/{session_id}/turns",
        params={"device_id": device_id},
    )
    assert turns_response.status_code == 200
    turns_payload = turns_response.json()
    assert turns_payload["total"] >= 2
    assert turns_payload["turns"][-1]["execution_state"]["stats"]["cache_hits"] >= 1


def test_agent_chat_reuses_same_client_request_id_without_duplicate_messages(monkeypatch):
    from services import agent_runtime

    class _SlowFakeAIClient(_FakeAIClient):
        async def generate_content(
            self,
            prompt: str,
            max_tokens: int,
            temperature: float,
            timeout: int,
            use_heavy: bool,
            preferred_provider: str | None = None,
            preferred_model: str | None = None,
        ) -> str:
            await asyncio.sleep(0.25)
            return await super().generate_content(
                prompt,
                max_tokens,
                temperature,
                timeout,
                use_heavy,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
            )

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _SlowFakeAIClient())

    client = TestClient(app)
    device_id = f"agent-dedupe-{uuid4().hex}"
    create_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Duplicate request test",
            "agent_type": "tutor",
        },
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]
    client_request_id = f"req-{uuid4().hex}"

    payload = {
        "session_id": session_id,
        "device_id": device_id,
        "client_request_id": client_request_id,
        "message": "Please give me a concise review plan.",
        "agent_type": "tutor",
    }

    def _send_request():
        threaded_client = TestClient(app)
        try:
            return threaded_client.post("/api/agent/chat", json=payload)
        finally:
            threaded_client.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_send_request)
        second_future = executor.submit(_send_request)
        first_response = first_future.result()
        second_response = second_future.result()

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_payload = first_response.json()
    second_payload = second_response.json()
    assert first_payload["trace_id"] == client_request_id
    assert second_payload["trace_id"] == client_request_id
    assert first_payload["session"]["id"] == session_id
    assert second_payload["session"]["id"] == session_id
    assert first_payload["assistant_message"]["id"] == second_payload["assistant_message"]["id"]
    assert first_payload["user_message"]["id"] == second_payload["user_message"]["id"]

    history_response = client.get(
        f"/api/agent/sessions/{session_id}/messages",
        params={"device_id": device_id},
    )
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["total"] == 2
    assert [message["role"] for message in history["messages"]] == ["user", "assistant"]


def test_agent_chat_reuses_same_client_request_id_without_session_id(monkeypatch):
    from services import agent_runtime

    class _SlowFakeAIClient(_FakeAIClient):
        async def generate_content(
            self,
            prompt: str,
            max_tokens: int,
            temperature: float,
            timeout: int,
            use_heavy: bool,
            preferred_provider: str | None = None,
            preferred_model: str | None = None,
        ) -> str:
            await asyncio.sleep(0.25)
            return await super().generate_content(
                prompt,
                max_tokens,
                temperature,
                timeout,
                use_heavy,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
            )

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _SlowFakeAIClient())

    client = TestClient(app)
    device_id = f"agent-dedupe-first-{uuid4().hex}"
    client_request_id = f"req-{uuid4().hex}"
    payload = {
        "device_id": device_id,
        "client_request_id": client_request_id,
        "message": "Please give me a concise review plan.",
        "agent_type": "tutor",
    }

    def _send_request():
        threaded_client = TestClient(app)
        try:
            return threaded_client.post("/api/agent/chat", json=payload)
        finally:
            threaded_client.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_send_request)
        second_future = executor.submit(_send_request)
        first_response = first_future.result()
        second_response = second_future.result()

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_payload = first_response.json()
    second_payload = second_response.json()
    session_id = first_payload["session"]["id"]

    assert first_payload["trace_id"] == client_request_id
    assert second_payload["trace_id"] == client_request_id
    assert second_payload["session"]["id"] == session_id
    assert first_payload["assistant_message"]["id"] == second_payload["assistant_message"]["id"]
    assert first_payload["user_message"]["id"] == second_payload["user_message"]["id"]

    history_response = client.get(
        f"/api/agent/sessions/{session_id}/messages",
        params={"device_id": device_id},
    )
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["total"] == 2
    assert [message["role"] for message in history["messages"]] == ["user", "assistant"]

    sessions_response = client.get(
        "/api/agent/sessions",
        params={"device_id": device_id, "status": "active"},
    )
    assert sessions_response.status_code == 200
    sessions = sessions_response.json()["sessions"]
    assert len([item for item in sessions if item["id"] == session_id]) == 1


def test_agent_tools_scope_learning_data_by_device(monkeypatch):
    from learning_tracking_models import LearningSession, QuestionRecord, WrongAnswerRetry, WrongAnswerV2
    from models import Chapter, ConceptMastery, DailyUpload, SessionLocal, TestRecord, init_db
    from services.agent_tools import execute_agent_tool
    from services.data_identity import clear_identity_caches_for_tests, ensure_learning_identity_schema

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    clear_identity_caches_for_tests()
    init_db()
    ensure_learning_identity_schema()

    now = datetime.now()
    today = date.today()
    device_a = f"agent-scope-a-{uuid4().hex}"
    device_b = f"agent-scope-b-{uuid4().hex}"
    chapter_id = f"chapter-{uuid4().hex}"
    session_a_id = f"session-a-{uuid4().hex}"
    session_b_id = f"session-b-{uuid4().hex}"
    concept_a_id = f"concept-a-{uuid4().hex}"
    concept_b_id = f"concept-b-{uuid4().hex}"

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=chapter_id,
                book="Internal Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiology",
                concepts=[],
                first_uploaded=today,
            )
        )
        db.add_all(
            [
                DailyUpload(
                    device_id=device_a,
                    date=today,
                    raw_content="device a upload",
                    ai_extracted={"book": "Internal Medicine", "chapter_title": "Cardiology"},
                ),
                DailyUpload(
                    device_id=device_b,
                    date=today - timedelta(days=1),
                    raw_content="device b upload",
                    ai_extracted={"book": "Pathology", "chapter_title": "Inflammation"},
                ),
                LearningSession(
                    id=session_a_id,
                    device_id=device_a,
                    session_type="exam",
                    chapter_id=chapter_id,
                    title="Device A Session",
                    status="completed",
                    total_questions=2,
                    correct_count=2,
                    wrong_count=0,
                    score=100,
                    accuracy=1.0,
                    started_at=now - timedelta(hours=2),
                    completed_at=now - timedelta(hours=1, minutes=45),
                    duration_seconds=900,
                ),
                LearningSession(
                    id=session_b_id,
                    device_id=device_b,
                    session_type="detail_practice",
                    chapter_id=chapter_id,
                    title="Device B Session",
                    status="completed",
                    total_questions=2,
                    correct_count=0,
                    wrong_count=2,
                    score=0,
                    accuracy=0.0,
                    started_at=now - timedelta(days=1),
                    completed_at=now - timedelta(days=1, minutes=-15),
                    duration_seconds=900,
                ),
                QuestionRecord(
                    session_id=session_a_id,
                    device_id=device_a,
                    question_index=0,
                    question_type="A1",
                    difficulty="基础",
                    question_text="Question A1",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    key_point="Cardiac output",
                    answered_at=now - timedelta(hours=2),
                    time_spent_seconds=30,
                ),
                QuestionRecord(
                    session_id=session_a_id,
                    device_id=device_a,
                    question_index=1,
                    question_type="A2",
                    difficulty="提高",
                    question_text="Question A2",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="B",
                    user_answer="B",
                    is_correct=True,
                    confidence="sure",
                    key_point="Stroke volume",
                    answered_at=now - timedelta(hours=2),
                    time_spent_seconds=35,
                ),
                QuestionRecord(
                    session_id=session_b_id,
                    device_id=device_b,
                    question_index=0,
                    question_type="A1",
                    difficulty="难题",
                    question_text="Question B1",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    user_answer="B",
                    is_correct=False,
                    confidence="no",
                    key_point="Inflammation",
                    answered_at=now - timedelta(days=1),
                    time_spent_seconds=40,
                ),
                ConceptMastery(
                    concept_id=concept_a_id,
                    device_id=device_a,
                    chapter_id=chapter_id,
                    name="Cardiac output",
                    retention=0.9,
                    understanding=0.8,
                    application=0.85,
                    next_review=today + timedelta(days=3),
                ),
                ConceptMastery(
                    concept_id=concept_b_id,
                    device_id=device_b,
                    chapter_id=chapter_id,
                    name="Inflammation",
                    retention=0.2,
                    understanding=0.3,
                    application=0.25,
                    next_review=today,
                ),
                TestRecord(
                    device_id=device_a,
                    concept_id=concept_a_id,
                    test_type="ai_quiz",
                    ai_question="A test",
                    ai_options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    ai_correct_answer="A",
                    user_answer="A",
                    confidence="sure",
                    is_correct=True,
                    score=100,
                    tested_at=now - timedelta(hours=1),
                ),
                TestRecord(
                    device_id=device_b,
                    concept_id=concept_b_id,
                    test_type="ai_quiz",
                    ai_question="B test",
                    ai_options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    ai_correct_answer="A",
                    user_answer="B",
                    confidence="no",
                    is_correct=False,
                    score=0,
                    tested_at=now - timedelta(days=1),
                ),
                WrongAnswerV2(
                    device_id=device_a,
                    question_fingerprint=f"fp-a-{uuid4().hex}",
                    question_text="Wrong A",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    explanation="A",
                    key_point="Cardiac output",
                    question_type="A1",
                    difficulty="基础",
                    chapter_id=chapter_id,
                    error_count=1,
                    encounter_count=1,
                    severity_tag="critical",
                    mastery_status="active",
                    next_review_date=today,
                    first_wrong_at=now - timedelta(hours=1),
                    last_wrong_at=now - timedelta(hours=1),
                ),
                WrongAnswerV2(
                    device_id=device_b,
                    question_fingerprint=f"fp-b-{uuid4().hex}",
                    question_text="Wrong B",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    explanation="B",
                    key_point="Inflammation",
                    question_type="A2",
                    difficulty="难题",
                    chapter_id=chapter_id,
                    error_count=2,
                    encounter_count=2,
                    severity_tag="stubborn",
                    mastery_status="active",
                    next_review_date=today,
                    first_wrong_at=now - timedelta(days=1),
                    last_wrong_at=now - timedelta(days=1),
                ),
            ]
        )
        db.commit()

        wrong_a = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_a).first()
        wrong_b = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_b).first()
        db.add_all(
            [
                WrongAnswerRetry(
                    device_id=device_a,
                    wrong_answer_id=int(wrong_a.id),
                    user_answer="A",
                    is_correct=True,
                    confidence="sure",
                    retried_at=now,
                ),
                WrongAnswerRetry(
                    device_id=device_b,
                    wrong_answer_id=int(wrong_b.id),
                    user_answer="B",
                    is_correct=False,
                    confidence="no",
                    retried_at=now,
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        _, progress_payload, _ = asyncio.run(
            execute_agent_tool("get_progress_summary", db, {"period": "30d"}, device_id=device_a)
        )
        _, sessions_payload, _ = asyncio.run(
            execute_agent_tool("get_learning_sessions", db, {"limit": 5}, device_id=device_a)
        )
        _, wrong_payload, _ = asyncio.run(
            execute_agent_tool("get_wrong_answers", db, {"limit": 5}, device_id=device_a)
        )
        _, history_payload, _ = asyncio.run(
            execute_agent_tool("get_study_history", db, {"days": 30, "limit": 5}, device_id=device_a)
        )
        _, mastery_payload, _ = asyncio.run(
            execute_agent_tool("get_knowledge_mastery", db, {"limit": 3}, device_id=device_a)
        )
        _, review_payload, _ = asyncio.run(
            execute_agent_tool("get_review_pressure", db, {"daily_planned_review": 20}, device_id=device_a)
        )

    assert progress_payload["overview"]["total_sessions"] == 1
    assert [item["id"] for item in progress_payload["recent_sessions"]] == [session_a_id]
    assert sessions_payload["count"] == 1
    assert [item["session_id"] for item in sessions_payload["items"]] == [session_a_id]
    assert wrong_payload["count"] == 1
    assert [item["question_preview"] for item in wrong_payload["items"]] == ["Wrong A"]
    assert history_payload["total_uploads_in_window"] == 1
    assert [item["book"] for item in history_payload["recent_uploads"]] == ["Internal Medicine"]
    assert mastery_payload["total_concepts"] == 1
    assert [item["name"] for item in mastery_payload["weak_concepts"]] == ["Cardiac output"]
    assert review_payload["due_wrong_answers"] == 1
    assert review_payload["recent_test_accuracy"] == 100.0


def test_agent_wrong_answer_tool_reports_total_count_and_aggregate_weak_points(monkeypatch):
    from learning_tracking_models import WrongAnswerV2
    from models import Chapter, SessionLocal, init_db
    from services.agent_runtime import build_source_cards
    from services.agent_tools import execute_agent_tool
    from services.data_identity import clear_identity_caches_for_tests, ensure_learning_identity_schema

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    clear_identity_caches_for_tests()
    init_db()
    ensure_learning_identity_schema()
    device_id = f"agent-wrong-summary-{uuid4().hex}"
    today = date.today()
    now = datetime.now()
    chapter_a = f"{device_id}-chapter-a"
    chapter_b = f"{device_id}-chapter-b"

    with SessionLocal() as db:
        db.add_all(
            [
                Chapter(
                    id=chapter_a,
                    book="Internal Medicine",
                    edition="1",
                    chapter_number="1",
                    chapter_title="Circulation",
                    concepts=[],
                    first_uploaded=today,
                ),
                Chapter(
                    id=chapter_b,
                    book="Internal Medicine",
                    edition="1",
                    chapter_number="2",
                    chapter_title="Inflammation",
                    concepts=[],
                    first_uploaded=today,
                ),
                WrongAnswerV2(
                    device_id=device_id,
                    question_fingerprint=f"wa-1-{uuid4().hex}",
                    question_text="Hemodynamics 1",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    explanation="A",
                    key_point="Hemodynamics",
                    question_type="A1",
                    difficulty="基础",
                    chapter_id=chapter_a,
                    error_count=3,
                    encounter_count=3,
                    severity_tag="critical",
                    mastery_status="active",
                    next_review_date=today,
                    first_wrong_at=now - timedelta(days=2),
                    last_wrong_at=now - timedelta(hours=1),
                ),
                WrongAnswerV2(
                    device_id=device_id,
                    question_fingerprint=f"wa-2-{uuid4().hex}",
                    question_text="Hemodynamics 2",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    explanation="B",
                    key_point="Hemodynamics",
                    question_type="A2",
                    difficulty="提高",
                    chapter_id=chapter_a,
                    error_count=2,
                    encounter_count=2,
                    severity_tag="critical",
                    mastery_status="active",
                    next_review_date=today - timedelta(days=1),
                    first_wrong_at=now - timedelta(days=3),
                    last_wrong_at=now - timedelta(hours=2),
                ),
                WrongAnswerV2(
                    device_id=device_id,
                    question_fingerprint=f"wa-3-{uuid4().hex}",
                    question_text="Inflammation 1",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    explanation="C",
                    key_point="Inflammation",
                    question_type="A3",
                    difficulty="难题",
                    chapter_id=chapter_b,
                    error_count=1,
                    encounter_count=1,
                    severity_tag="stubborn",
                    mastery_status="active",
                    next_review_date=None,
                    first_wrong_at=now - timedelta(days=4),
                    last_wrong_at=now - timedelta(hours=3),
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        _, wrong_payload, _ = asyncio.run(
            execute_agent_tool("get_wrong_answers", db, {"limit": 2}, device_id=device_id)
        )

    assert wrong_payload["count"] == 3
    assert wrong_payload["returned_count"] == 2
    assert wrong_payload["sampled"] is True
    assert len(wrong_payload["items"]) == 2
    assert wrong_payload["severity_counts"]["critical"] == 2
    assert wrong_payload["severity_counts"]["stubborn"] == 1
    assert wrong_payload["due_count"] == 2
    assert [item["name"] for item in wrong_payload["top_key_points"][:2]] == ["Hemodynamics", "Inflammation"]
    assert wrong_payload["top_key_points"][0]["count"] == 2
    assert wrong_payload["top_key_points"][0]["error_total"] == 5
    assert wrong_payload["top_chapters"][0]["count"] == 2
    assert "Circulation" in wrong_payload["top_chapters"][0]["chapter_label"]

    cards = build_source_cards(["get_wrong_answers"], {"get_wrong_answers": wrong_payload})
    assert len(cards) == 1
    card = cards[0]
    assert card.count == 3
    assert "最近 2 条样本" in card.summary
    assert any("高频薄弱点" in bullet for bullet in card.bullets)


def test_agent_study_history_uses_session_uploaded_content_as_fallback(monkeypatch):
    from learning_tracking_models import LearningSession
    from models import Chapter, SessionLocal, init_db
    from services.agent_tools import execute_agent_tool
    from services.data_identity import clear_identity_caches_for_tests, ensure_learning_identity_schema

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    clear_identity_caches_for_tests()
    init_db()
    ensure_learning_identity_schema()
    device_id = f"agent-history-fallback-{uuid4().hex}"
    yesterday = date.today() - timedelta(days=1)
    now = datetime.now()
    chapter_id = f"{device_id}-chapter"

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=chapter_id,
                book="生理学",
                edition="1",
                chapter_number="5",
                chapter_title="肺通气",
                concepts=[],
                first_uploaded=yesterday,
            )
        )
        db.add(
            LearningSession(
                id=f"session-{uuid4().hex}",
                device_id=device_id,
                session_type="exam",
                chapter_id=chapter_id,
                title="医学考研模拟试卷（分段生成）",
                uploaded_content="肺通气原始讲义内容" * 30,
                status="completed",
                total_questions=10,
                correct_count=6,
                wrong_count=4,
                score=60,
                accuracy=0.6,
                started_at=now - timedelta(days=1),
                completed_at=now - timedelta(days=1, minutes=-20),
                duration_seconds=1200,
            )
        )
        db.commit()

    with SessionLocal() as db:
        _, history_payload, _ = asyncio.run(
            execute_agent_tool("get_study_history", db, {"days": 7, "limit": 3}, device_id=device_id)
        )

    assert history_payload["total_uploads_in_window"] == 1
    assert history_payload["weekly_uploads"] == 1
    assert history_payload["streak_days"] == 0
    assert history_payload["latest_study_date"] == yesterday.isoformat()
    assert history_payload["session_fallback_count_in_window"] == 1
    assert history_payload["daily_upload_count_in_window"] == 0
    assert history_payload["recent_uploads"][0]["source"] == "learning_session"
    assert history_payload["recent_uploads"][0]["book"] == "生理学"
    assert history_payload["recent_uploads"][0]["chapter_title"] == "肺通气"


def test_tracking_session_start_records_daily_upload_snapshot(monkeypatch):
    from models import Chapter, DailyUpload, SessionLocal, init_db
    from services.data_identity import clear_identity_caches_for_tests, ensure_learning_identity_schema

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    clear_identity_caches_for_tests()
    init_db()
    ensure_learning_identity_schema()

    client = TestClient(app)
    device_id = f"tracking-upload-{uuid4().hex}"
    chapter_id = f"{device_id}-chapter"
    today = date.today()
    uploaded_content = "肺通气原始讲义内容" * 20

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=chapter_id,
                book="生理学",
                edition="1",
                chapter_number="5",
                chapter_title="肺通气",
                concepts=[{"id": "kp-1", "name": "肺通气"}],
                first_uploaded=today,
            )
        )
        db.commit()

    headers = {"x-tls-device-id": device_id}
    response = client.post(
        "/api/tracking/session/start",
        json={
            "session_type": "exam",
            "chapter_id": chapter_id,
            "title": "医学考研模拟试卷（分段生成）",
            "uploaded_content": uploaded_content,
        },
        headers=headers,
    )
    assert response.status_code == 200

    duplicate_response = client.post(
        "/api/tracking/session/start",
        json={
            "session_type": "exam",
            "chapter_id": chapter_id,
            "title": "医学考研模拟试卷（分段生成）",
            "uploaded_content": uploaded_content,
        },
        headers=headers,
    )
    assert duplicate_response.status_code == 200

    with SessionLocal() as db:
        uploads = db.query(DailyUpload).filter(DailyUpload.device_id == device_id).all()

    assert len(uploads) == 1
    upload = uploads[0]
    assert upload.date == today
    assert upload.raw_content == uploaded_content
    assert upload.ai_extracted["book"] == "生理学"
    assert upload.ai_extracted["chapter_title"] == "肺通气"
    assert upload.ai_extracted["chapter_id"] == chapter_id


def test_agent_runtime_derives_topic_overrides_for_cell_electricity(monkeypatch):
    from models import Chapter, SessionLocal, init_db
    from services.agent_runtime import _derive_topic_tool_overrides
    from services.data_identity import clear_identity_caches_for_tests, ensure_learning_identity_schema

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    clear_identity_caches_for_tests()
    init_db()
    ensure_learning_identity_schema()

    chapter_a = f"topic-{uuid4().hex}-a"
    chapter_b = f"topic-{uuid4().hex}-b"
    with SessionLocal() as db:
        db.add_all(
            [
                Chapter(
                    id=chapter_a,
                    book="生理学",
                    edition="1",
                    chapter_number="04",
                    chapter_title="专题细胞电活动",
                    concepts=[],
                    first_uploaded=date.today(),
                ),
                Chapter(
                    id=chapter_b,
                    book="生理学",
                    edition="1",
                    chapter_number="10",
                    chapter_title="专题心肌电活动和特性",
                    concepts=[],
                    first_uploaded=date.today(),
                ),
            ]
        )
        db.commit()

        overrides = _derive_topic_tool_overrides(
            db,
            "我最近是不是专题细胞电活动学得不太好",
            ["get_learning_sessions", "get_wrong_answers", "get_knowledge_mastery", "get_study_history"],
        )

    assert overrides["get_learning_sessions"]["query"] == "专题细胞电活动"
    assert overrides["get_wrong_answers"]["query"] == "专题细胞电活动"
    assert set(overrides["get_learning_sessions"]["chapter_ids"]) >= {chapter_a, chapter_b}
    assert set(overrides["get_wrong_answers"]["chapter_ids"]) >= {chapter_a, chapter_b}
    assert set(overrides["get_knowledge_mastery"]["chapter_ids"]) >= {chapter_a, chapter_b}


def test_agent_chat_auto_expands_tools_for_topic_queries(monkeypatch):
    from learning_tracking_models import LearningSession, WrongAnswerV2
    from models import Chapter, SessionLocal, init_db
    from services import agent_runtime
    from services.data_identity import clear_identity_caches_for_tests, ensure_learning_identity_schema

    monkeypatch.delenv("SINGLE_USER_MODE", raising=False)
    clear_identity_caches_for_tests()
    init_db()
    ensure_learning_identity_schema()
    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())
    monkeypatch.setattr(agent_runtime, "resolve_requested_tools", lambda message, requested_tools: ["get_progress_summary"])

    device_id = f"agent-topic-chat-{uuid4().hex}"
    chapter_a = f"{device_id}-chapter-a"
    chapter_b = f"{device_id}-chapter-b"
    now = datetime.now()
    today = date.today()

    with SessionLocal() as db:
        db.add_all(
            [
                Chapter(
                    id=chapter_a,
                    book="生理学",
                    edition="1",
                    chapter_number="04",
                    chapter_title="细胞电活动",
                    concepts=[],
                    first_uploaded=today,
                ),
                Chapter(
                    id=chapter_b,
                    book="生理学",
                    edition="1",
                    chapter_number="10",
                    chapter_title="心肌电活动和特性",
                    concepts=[],
                    first_uploaded=today,
                ),
                LearningSession(
                    id=f"session-{uuid4().hex}",
                    device_id=device_id,
                    session_type="exam",
                    chapter_id=chapter_a,
                    title="细胞电活动专项测试",
                    uploaded_content="动作电位与静息电位讲义" * 20,
                    status="completed",
                    total_questions=10,
                    correct_count=4,
                    wrong_count=6,
                    score=40,
                    accuracy=0.4,
                    started_at=now - timedelta(days=1),
                    completed_at=now - timedelta(days=1, minutes=-15),
                    duration_seconds=900,
                ),
                WrongAnswerV2(
                    device_id=device_id,
                    question_fingerprint=f"topic-wa-{uuid4().hex}",
                    question_text="关于动作电位0期去极化机制的判断，正确的是？",
                    options={"A": "1", "B": "2", "C": "3", "D": "4"},
                    correct_answer="A",
                    explanation="A",
                    key_point="动作电位",
                    question_type="A1",
                    difficulty="基础",
                    chapter_id=chapter_b,
                    error_count=2,
                    encounter_count=2,
                    severity_tag="critical",
                    mastery_status="active",
                    next_review_date=today,
                    first_wrong_at=now - timedelta(days=1),
                    last_wrong_at=now - timedelta(days=1),
                ),
            ]
        )
        db.commit()

    client = TestClient(app)
    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "我最近是不是细胞电活动学得不太好",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    assert "get_progress_summary" in structured["selected_tools"]
    assert "get_learning_sessions" in structured["selected_tools"]
    assert "get_wrong_answers" in structured["selected_tools"]
    assert "get_study_history" in structured["selected_tools"]
    assert "get_knowledge_mastery" in structured["selected_tools"]

    learning_call = next(item for item in payload["tool_calls"] if item["tool_name"] == "get_learning_sessions")
    wrong_call = next(item for item in payload["tool_calls"] if item["tool_name"] == "get_wrong_answers")
    mastery_call = next(item for item in payload["tool_calls"] if item["tool_name"] == "get_knowledge_mastery")

    assert learning_call["tool_args"]["query"] == "细胞电活动"
    assert set(learning_call["tool_args"]["chapter_ids"]) >= {chapter_a, chapter_b}
    assert wrong_call["tool_args"]["query"] == "细胞电活动"
    assert set(wrong_call["tool_args"]["chapter_ids"]) >= {chapter_a, chapter_b}
    assert set(mastery_call["tool_args"]["chapter_ids"]) >= {chapter_a, chapter_b}


def test_agent_chat_auto_expands_tools_for_plan_requests(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())
    monkeypatch.setattr(agent_runtime, "resolve_requested_tools", lambda message, requested_tools: ["get_progress_summary"])

    client = TestClient(app)
    device_id = f"agent-auto-expand-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "结合我的上传历史、未来趋势和今天的复习计划，帮我拆解接下来的安排。",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    assert "get_progress_summary" in structured["selected_tools"]
    assert "get_review_pressure" in structured["selected_tools"]
    assert "get_wrong_answers" in structured["selected_tools"]
    assert "get_study_history" in structured["selected_tools"]


def test_agent_knowledge_mastery_prioritizes_measured_concepts():
    from models import Chapter, ConceptMastery, SessionLocal, init_db
    from services.agent_tools import execute_agent_tool
    from services.data_identity import ensure_learning_identity_schema

    init_db()
    ensure_learning_identity_schema()
    device_id = f"agent-mastery-{uuid4().hex}"
    today = date.today()

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=f"{device_id}-chapter",
                book="Internal Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiology",
                concepts=[],
                first_uploaded=today,
            )
        )
        db.add_all(
            [
                ConceptMastery(
                    concept_id=f"{device_id}-measured",
                    device_id=device_id,
                    chapter_id=f"{device_id}-chapter",
                    name="Measured Concept",
                    retention=0.2,
                    understanding=0.1,
                    application=0.1,
                    last_tested=today - timedelta(days=1),
                    next_review=today,
                ),
                ConceptMastery(
                    concept_id=f"{device_id}-unmeasured",
                    device_id=device_id,
                    chapter_id=f"{device_id}-chapter",
                    name="Unmeasured Concept",
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        _, mastery_payload, _ = asyncio.run(
            execute_agent_tool("get_knowledge_mastery", db, {"limit": 3}, device_id=device_id)
        )

    assert mastery_payload["total_concepts"] == 2
    assert mastery_payload["measured_concepts"] == 1
    assert mastery_payload["unmeasured_concepts"] == 1
    assert [item["name"] for item in mastery_payload["weak_concepts"]] == ["Measured Concept"]


def test_agent_knowledge_mastery_ignores_placeholder_chapters_when_real_data_exists():
    from models import Chapter, ConceptMastery, SessionLocal, init_db
    from services.agent_tools import execute_agent_tool
    from services.data_identity import ensure_learning_identity_schema

    init_db()
    ensure_learning_identity_schema()
    device_id = f"agent-placeholder-{uuid4().hex}"
    today = date.today()

    with SessionLocal() as db:
        db.add(
            Chapter(
                id=f"{device_id}-chapter",
                book="Internal Medicine",
                edition="1",
                chapter_number="1",
                chapter_title="Cardiology",
                concepts=[],
                first_uploaded=today,
            )
        )
        if db.query(Chapter).filter(Chapter.id == "0").first() is None:
            db.add(
                Chapter(
                    id="0",
                    book="Uncategorized",
                    edition="1",
                    chapter_number="0",
                    chapter_title="Placeholder",
                    concepts=[],
                    first_uploaded=today,
                )
            )
        db.add_all(
            [
                ConceptMastery(
                    concept_id=f"{device_id}-real",
                    device_id=device_id,
                    chapter_id=f"{device_id}-chapter",
                    name="Real Concept",
                    retention=0.3,
                    understanding=0.4,
                    application=0.5,
                    last_tested=today - timedelta(days=1),
                    next_review=today,
                ),
                ConceptMastery(
                    concept_id=f"{device_id}-placeholder",
                    device_id=device_id,
                    chapter_id="0",
                    name="Placeholder Concept",
                    retention=0.0,
                    understanding=0.0,
                    application=0.0,
                    last_tested=today - timedelta(days=1),
                    next_review=today,
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        _, mastery_payload, _ = asyncio.run(
            execute_agent_tool("get_knowledge_mastery", db, {"limit": 3}, device_id=device_id)
        )

    assert mastery_payload["total_concepts"] == 1
    assert mastery_payload["measured_concepts"] == 1
    assert mastery_payload["unmeasured_concepts"] == 0
    assert [item["name"] for item in mastery_payload["weak_concepts"]] == ["Real Concept"]
    assert all(item["chapter_id"] != "0" for item in mastery_payload["weak_chapters"])


def test_agent_chat_uses_llm_planner_when_available(monkeypatch):
    from services import agent_runtime

    class _PlannerAwareFakeAIClient(_FakeAIClient):
        def __init__(self):
            self.json_calls = 0
            self.follow_up_calls = 0
            self.response_strategy_calls = 0

        async def generate_json(
            self,
            prompt,
            schema,
            max_tokens=4000,
            temperature=0.2,
            timeout=150,
            use_heavy=False,
            preferred_provider=None,
            preferred_model=None,
        ):
            assert preferred_provider == "deepseek"
            assert preferred_model == "deepseek-chat"
            self.json_calls += 1
            if "学习 agent 的回答策略规划器" in prompt:
                self.response_strategy_calls += 1
                return {
                    "strategy": "answer_with_caveat",
                    "reason": "当前数据覆盖主体，但仍需保守表述。",
                    "instruction": "先给结论，再说明证据边界，最后给下一步建议。",
                    "clarifying_questions": [],
                }

            assert "学习 agent 的数据调度器" in prompt
            self.follow_up_calls += 1
            if self.follow_up_calls == 1:
                return {
                    "should_continue": True,
                    "decision_reason": "还缺高风险错题证据。",
                    "next_tools": [
                        {
                            "tool_name": "get_wrong_answers",
                            "reason": "补充高风险错题证据。",
                        }
                    ],
                }
            return {
                "should_continue": False,
                "decision_reason": "当前数据已经足够回答。",
                "next_tools": [],
            }

    planner_client = _PlannerAwareFakeAIClient()
    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: planner_client)
    monkeypatch.setattr(agent_runtime, "resolve_requested_tools", lambda message, requested_tools: ["get_progress_summary"])

    client = TestClient(app)
    device_id = f"agent-llm-planner-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "帮我分析今天先复习什么。",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    assert "get_progress_summary" in structured["selected_tools"]
    assert "get_wrong_answers" in structured["selected_tools"]
    assert structured["execution_state"]["stats"]["iteration_count"] == 2
    assert structured["execution_state"]["plan_versions"][0]["decision_source"] == "llm"
    assert structured["execution_state"]["plan_versions"][1]["decision_source"] == "llm"
    assert structured["response_strategy"]["strategy"] == "answer_with_caveat"
    assert structured["execution_state"]["response_strategy"]["strategy"] == "answer_with_caveat"
    assert planner_client.follow_up_calls >= 2
    assert planner_client.response_strategy_calls == 1
    assert planner_client.json_calls >= 3


def test_agent_chat_persists_response_strategy(monkeypatch):
    from services import agent_runtime

    class _ResponseStrategyFakeAIClient(_FakeAIClient):
        async def generate_json(
            self,
            prompt,
            schema,
            max_tokens=4000,
            temperature=0.2,
            timeout=150,
            use_heavy=False,
            preferred_provider=None,
            preferred_model=None,
        ):
            assert preferred_provider == "deepseek"
            assert preferred_model == "deepseek-chat"
            if "学习 agent 的回答策略规划器" in prompt:
                return {
                    "strategy": "clarify",
                    "reason": "用户目标还不够聚焦。",
                    "instruction": "先发出澄清问题，不要直接给方案。",
                    "clarifying_questions": [
                        "你想先看今天的安排，还是本周的整体安排？",
                        "你更关心错题、进度，还是复习压力？",
                    ],
                }
            return {
                "should_continue": False,
                "decision_reason": "当前工具已经足够。",
                "next_tools": [],
            }

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _ResponseStrategyFakeAIClient())
    monkeypatch.setattr(
        agent_runtime,
        "resolve_requested_tools",
        lambda message, requested_tools: ["get_progress_summary"],
    )

    client = TestClient(app)
    device_id = f"agent-response-strategy-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "先帮我看看这个",
            "agent_type": "tutor",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    assert structured["response_strategy"]["strategy"] == "clarify"
    assert len(structured["response_strategy"]["clarifying_questions"]) == 2
    assert structured["execution_state"]["response_strategy"]["strategy"] == "clarify"
    assert payload["user_message"]["content_structured"]["response_strategy"]["strategy"] == "clarify"


def test_agent_chat_stream_returns_sse_events(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    client = TestClient(app)
    device_id = f"agent-stream-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with client.stream(
        "POST",
        "/api/agent/chat/stream",
        json={
            "device_id": device_id,
            "message": "结合我的学习进度，给我一个今天的复习建议。",
            "agent_type": "tutor",
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: ready" in body
    assert "event: message_start" in body
    assert body.count("event: delta") >= 2
    assert "event: done" in body
    assert "这是一个基于学习数据生成的测试回答。" in body
    assert '"plan"' in body


def test_agent_tools_include_write_action_metadata():
    client = TestClient(app)

    response = client.get("/api/agent/tools")

    assert response.status_code == 200
    payload = response.json()
    tools_by_name = {item["name"]: item for item in payload}

    assert tools_by_name["get_progress_summary"]["tool_type"] == "read"
    assert tools_by_name["get_progress_summary"]["risk_level"] == "low"
    assert tools_by_name["consult_openmanus"]["tool_type"] == "read"
    assert tools_by_name["consult_openmanus"]["risk_level"] == "medium"
    assert tools_by_name["consult_openmanus"]["requires_confirmation"] is False
    assert tools_by_name["create_daily_review_paper"]["tool_type"] == "write"
    assert tools_by_name["create_daily_review_paper"]["risk_level"] == "medium"
    assert tools_by_name["create_daily_review_paper"]["requires_confirmation"] is True
    assert tools_by_name["generate_quiz_set"]["tool_type"] == "write"
    assert tools_by_name["generate_quiz_set"]["risk_level"] == "high"
    assert tools_by_name["generate_quiz_set"]["requires_confirmation"] is True
    assert tools_by_name["update_concept_mastery"]["tool_type"] == "write"
    assert tools_by_name["update_concept_mastery"]["risk_level"] == "medium"
    assert tools_by_name["update_concept_mastery"]["requires_confirmation"] is True
    assert tools_by_name["log_agent_decision"]["tool_type"] == "write"
    assert tools_by_name["log_agent_decision"]["risk_level"] == "low"
    assert tools_by_name["log_agent_decision"]["requires_confirmation"] is False


def test_agent_chat_can_use_requested_openmanus_tool(monkeypatch):
    from services import agent_runtime
    from services import agent_tools

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())
    monkeypatch.setattr(
        agent_tools,
        "run_openmanus_consult",
        lambda query, max_steps=4, timeout_seconds=None: {
            "status": "completed",
            "query": query,
            "answer": "OpenManus 建议先复盘高风险错题，再安排一轮 30 分钟巩固。",
            "tool_names": ["terminate"],
            "steps_executed": 1,
            "message_count": 4,
            "assistant_message_count": 1,
            "run_result": "Step 1: terminate",
            "count": 1,
        },
    )

    client = TestClient(app)
    device_id = f"agent-openmanus-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "请用 OpenManus 帮我拆一个今晚的复习计划。",
            "agent_type": "tutor",
            "requested_tools": ["consult_openmanus"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    sources = structured["sources"]

    assert "consult_openmanus" in structured["selected_tools"]
    assert any(item["tool_name"] == "consult_openmanus" for item in payload["tool_calls"])
    assert any(item["tool_name"] == "consult_openmanus" for item in sources)


def test_agent_chat_structured_payload_includes_action_suggestions(monkeypatch):
    from services import agent_runtime

    monkeypatch.setattr(agent_runtime, "get_ai_client", lambda: _FakeAIClient())

    client = TestClient(app)
    device_id = f"agent-suggestions-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    response = client.post(
        "/api/agent/chat",
        json={
            "device_id": device_id,
            "message": "结合我的错题和知识点掌握情况，给我一份可以直接执行的复习建议。",
            "agent_type": "tutor",
            "requested_tools": ["get_wrong_answers", "get_knowledge_mastery"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    structured = payload["assistant_message"]["content_structured"]
    suggestions = structured["action_suggestions"]
    suggestion_names = [item["tool_name"] for item in suggestions]

    assert suggestion_names
    assert "create_daily_review_paper" in suggestion_names
    assert "generate_quiz_set" in suggestion_names
    assert "update_concept_mastery" in suggestion_names
    assert any(item["tool_args"].get("wrong_answer_ids") for item in suggestions if item["tool_name"] == "create_daily_review_paper")
    assert any(item["tool_args"].get("concept_ids") for item in suggestions if item["tool_name"] == "generate_quiz_set")


def test_agent_action_log_decision_executes_without_confirmation():
    client = TestClient(app)
    device_id = f"agent-action-log-{uuid4().hex}"

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Action log session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]

    response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "log_agent_decision",
            "tool_args": {
                "decision_type": "plan",
                "summary": "决定先补动作执行接口，再补前端动作卡片。",
                "rationale": "先把后端执行链路和审计闭环打通。",
                "metadata": {"phase": "phase1"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["executed"] is True
    assert payload["requires_confirmation"] is False
    assert payload["action"]["approval_status"] == "auto"
    assert payload["action"]["execution_status"] == "success"
    assert payload["action"]["verification_status"] == "verified"
    assert payload["action"]["result"]["logged"] is True
    assert payload["action"]["result"]["decision_type"] == "plan"


def test_agent_action_update_wrong_answer_status_preview_then_confirm():
    from learning_tracking_models import WrongAnswerV2
    from models import SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-wa-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_id).first()
        assert wrong_answer is not None
        wrong_answer_id = int(wrong_answer.id)

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Wrong answer action session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "update_wrong_answer_status",
            "tool_args": {
                "wrong_answer_ids": [wrong_answer_id],
                "target_status": "archived",
                "reason": "复习完成，进入归档。",
            },
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["executed"] is False
    assert preview_payload["requires_confirmation"] is True
    assert preview_payload["action"]["approval_status"] == "pending"
    assert preview_payload["action"]["execution_status"] == "pending"

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": preview_payload["action"]["id"],
            "confirm": True,
        },
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["executed"] is True
    assert confirm_payload["action"]["approval_status"] == "approved"
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["verification_status"] == "verified"
    assert confirm_payload["action"]["result"]["applied_target_status"] == "archived"

    with SessionLocal() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_answer_id).first()
        assert refreshed is not None
        assert refreshed.mastery_status == "archived"


def test_agent_action_update_concept_mastery_preview_then_confirm():
    from models import ConceptMastery, SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-concept-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        concept = db.query(ConceptMastery).filter(ConceptMastery.device_id == device_id).first()
        assert concept is not None
        concept_id = concept.concept_id

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Concept mastery action session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "update_concept_mastery",
            "tool_args": {
                "concept_ids": [concept_id],
                "review_in_days": 5,
                "reason": "根据最近答题结果校准掌握度",
            },
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["executed"] is False
    assert preview_payload["requires_confirmation"] is True
    assert preview_payload["action"]["approval_status"] == "pending"
    assert preview_payload["preview_summary"].startswith("将回写 1 个知识点掌握度")

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": preview_payload["action"]["id"],
            "confirm": True,
        },
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    result = confirm_payload["action"]["result"]
    concept_result = result["concepts"][0]

    assert confirm_payload["executed"] is True
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["verification_status"] == "verified"
    assert result["updated_count"] == 1
    assert result["review_in_days"] == 5
    assert result["reason"] == "根据最近答题结果校准掌握度"
    assert concept_result["concept_id"] == concept_id
    assert concept_result["verified"] is True

    with SessionLocal() as db:
        refreshed = db.query(ConceptMastery).filter(ConceptMastery.concept_id == concept_id).first()
        assert refreshed is not None
        assert round(float(refreshed.retention or 0), 4) == round(float(concept_result["retention"]), 4)
        assert round(float(refreshed.understanding or 0), 4) == round(float(concept_result["understanding"]), 4)
        assert round(float(refreshed.application or 0), 4) == round(float(concept_result["application"]), 4)
        assert refreshed.next_review.isoformat() == concept_result["next_review"]


def test_agent_action_confirm_rejects_param_mutation():
    from learning_tracking_models import WrongAnswerV2
    from models import SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-mutate-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_id).first()
        assert wrong_answer is not None
        wrong_answer_id = int(wrong_answer.id)

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Mutation guard session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "update_wrong_answer_status",
            "tool_args": {
                "wrong_answer_ids": [wrong_answer_id],
                "target_status": "archived",
            },
        },
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": preview_payload["action"]["id"],
            "confirm": True,
            "tool_args": {
                "wrong_answer_ids": [wrong_answer_id],
                "target_status": "active",
            },
        },
    )

    assert confirm_response.status_code == 400

    with SessionLocal() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_answer_id).first()
        assert refreshed is not None
        assert refreshed.mastery_status == "active"


def test_agent_action_confirm_rejects_stale_daily_review_preview():
    from learning_tracking_models import DailyReviewPaper, WrongAnswerV2
    from models import SessionLocal
    from services.data_identity import build_actor_key

    client = TestClient(app)
    device_id = f"agent-action-stale-{uuid4().hex}"
    first_id = _seed_scoped_wrong_answer(
        device_id=device_id,
        question_text="Daily review stale A",
        key_point="stale-kp-a",
        next_review_offset_days=0,
    )
    _seed_scoped_wrong_answer(
        device_id=device_id,
        question_text="Daily review stale B",
        key_point="stale-kp-b",
        next_review_offset_days=1,
    )

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Stale preview session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]
    paper_date = (date.today() + timedelta(days=17)).isoformat()

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "create_daily_review_paper",
            "tool_args": {
                "paper_date": paper_date,
                "target_count": 1,
            },
        },
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()

    with SessionLocal() as db:
        wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == first_id).first()
        assert wrong_answer is not None
        wrong_answer.mastery_status = "archived"
        db.commit()

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": preview_payload["action"]["id"],
            "confirm": True,
        },
    )

    assert confirm_response.status_code == 400

    with SessionLocal() as db:
        paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.actor_key == build_actor_key(None, device_id),
            )
            .first()
        )
        assert paper is None


def test_agent_action_confirm_legacy_pending_without_preview_context():
    from agent_models import AgentActionLog
    from learning_tracking_models import WrongAnswerV2
    from models import SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-legacy-pending-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Legacy pending session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]
    action_id = uuid4().hex

    with SessionLocal() as db:
        wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_id).first()
        assert wrong_answer is not None
        db.add(
            AgentActionLog(
                id=action_id,
                session_id=session_id,
                device_id=device_id,
                tool_name="update_wrong_answer_status",
                tool_type="write",
                tool_args={
                    "wrong_answer_ids": [int(wrong_answer.id)],
                    "target_status": "archived",
                    "reason": "legacy pending action",
                },
                risk_level="medium",
                approval_status="pending",
                execution_status="pending",
                triggered_by="user_request",
                preview_summary="legacy pending preview",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
        db.commit()

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": action_id,
            "confirm": True,
        },
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["executed"] is True
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["verification_status"] == "verified"

    with SessionLocal() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_id).first()
        assert refreshed is not None
        assert refreshed.mastery_status == "archived"


def test_agent_action_create_daily_review_paper_preview_then_confirm_and_list():
    from learning_tracking_models import DailyReviewPaper
    from models import SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-paper-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Daily review action session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]
    paper_date = (date.today() + timedelta(days=14)).isoformat()

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "create_daily_review_paper",
            "tool_args": {
                "paper_date": paper_date,
                "target_count": 1,
            },
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["executed"] is False
    assert preview_payload["requires_confirmation"] is True
    assert preview_payload["action"]["approval_status"] == "pending"
    assert preview_payload["preview_summary"].startswith(f"为 {paper_date} 生成 1 道每日复习")

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": preview_payload["action"]["id"],
            "confirm": True,
        },
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["executed"] is True
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["verification_status"] == "verified"
    assert confirm_payload["action"]["result"]["paper_date"] == paper_date
    assert confirm_payload["action"]["result"]["total_questions"] == 1
    assert confirm_payload["action"]["result"]["config"]["target_count"] == 1

    with SessionLocal() as db:
        paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.device_id == device_id,
            )
            .first()
        )
        assert paper is not None
        assert paper.total_questions == 1
        assert len(paper.items) == 1
        assert paper.config["target_count"] == 1

    actions_response = client.get(
        f"/api/agent/sessions/{session_id}/actions",
        params={"device_id": device_id},
    )
    assert actions_response.status_code == 200
    actions_payload = actions_response.json()
    assert actions_payload["total"] >= 1
    assert actions_payload["actions"][0]["tool_name"] == "create_daily_review_paper"


def test_agent_action_generate_quiz_set_preview_then_confirm():
    from learning_tracking_models import LearningSession, QuestionRecord
    from models import ConceptMastery, QuizSession, SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-quiz-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        concept = db.query(ConceptMastery).filter(ConceptMastery.device_id == device_id).first()
        assert concept is not None
        concept_id = concept.concept_id

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "device_id": device_id,
            "title": "Quiz set action session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "tool_name": "generate_quiz_set",
            "tool_args": {
                "concept_ids": [concept_id],
                "target_count": 4,
                "session_type": "practice",
                "title": "Agent 定向巩固题组",
            },
        },
    )

    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["executed"] is False
    assert preview_payload["requires_confirmation"] is True
    assert preview_payload["action"]["approval_status"] == "pending"
    assert preview_payload["preview_summary"].startswith("将为 1 个知识点生成")

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "device_id": device_id,
            "action_id": preview_payload["action"]["id"],
            "confirm": True,
        },
    )

    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    result = confirm_payload["action"]["result"]
    total_questions = result["total_questions"]

    assert confirm_payload["executed"] is True
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["verification_status"] == "verified"
    assert result["session_type"] == "practice"
    assert result["concept_ids"] == [concept_id]
    assert result["title"] == "Agent 定向巩固题组"
    assert total_questions > 0
    assert len(result["question_record_ids"]) == total_questions

    with SessionLocal() as db:
        quiz_session = db.query(QuizSession).filter(QuizSession.id == result["quiz_session_id"]).first()
        learning_session = db.query(LearningSession).filter(LearningSession.id == result["learning_session_id"]).first()
        question_records = (
            db.query(QuestionRecord)
            .filter(QuestionRecord.session_id == result["learning_session_id"])
            .order_by(QuestionRecord.question_index.asc(), QuestionRecord.id.asc())
            .all()
        )

        assert quiz_session is not None
        assert learning_session is not None
        assert quiz_session.session_type == "practice"
        assert learning_session.session_type == "detail_practice"
        assert learning_session.exam_id == str(result["quiz_session_id"])
        assert learning_session.title == "Agent 定向巩固题组"
        assert len(quiz_session.questions or []) == total_questions
        assert len(question_records) == total_questions
        assert [int(item.id) for item in question_records] == result["question_record_ids"]


def test_agent_action_update_wrong_answer_status_confirm_then_rollback():
    from learning_tracking_models import WrongAnswerV2
    from models import SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-wa-rollback-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_id).first()
        assert wrong_answer is not None
        wrong_answer_id = int(wrong_answer.id)
        original_archived_at = wrong_answer.archived_at

    session_id = _create_agent_action_session(
        client,
        title="Wrong answer rollback session",
        device_id=device_id,
    )

    preview_response = _preview_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        tool_name="update_wrong_answer_status",
        tool_args={
            "wrong_answer_ids": [wrong_answer_id],
            "target_status": "archived",
            "reason": "rollback test",
        },
    )
    assert preview_response.status_code == 200
    action_id = preview_response.json()["action"]["id"]

    confirm_response = _confirm_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["can_rollback"] is True

    rollback_response = _rollback_agent_action_request(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["executed"] is False
    assert rollback_payload["action"]["execution_status"] == "rolled_back"
    assert rollback_payload["action"]["can_rollback"] is False
    assert rollback_payload["action"]["result"]["rollback"]["restored_statuses"][str(wrong_answer_id)] == "active"

    with SessionLocal() as db:
        refreshed = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == wrong_answer_id).first()
        assert refreshed is not None
        assert refreshed.mastery_status == "active"
        assert refreshed.archived_at == original_archived_at


def test_agent_action_update_concept_mastery_confirm_then_rollback():
    from models import ConceptMastery, SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-concept-rollback-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        concept = db.query(ConceptMastery).filter(ConceptMastery.device_id == device_id).first()
        assert concept is not None
        concept_id = concept.concept_id
        original_snapshot = {
            "retention": round(float(concept.retention or 0.0), 4),
            "understanding": round(float(concept.understanding or 0.0), 4),
            "application": round(float(concept.application or 0.0), 4),
            "last_tested": concept.last_tested.isoformat() if concept.last_tested else None,
            "next_review": concept.next_review.isoformat() if concept.next_review else None,
        }

    session_id = _create_agent_action_session(
        client,
        title="Concept rollback session",
        device_id=device_id,
    )

    preview_response = _preview_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        tool_name="update_concept_mastery",
        tool_args={
            "concept_ids": [concept_id],
            "review_in_days": 5,
            "reason": "rollback test",
        },
    )
    assert preview_response.status_code == 200
    action_id = preview_response.json()["action"]["id"]

    confirm_response = _confirm_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["can_rollback"] is True

    rollback_response = _rollback_agent_action_request(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["action"]["execution_status"] == "rolled_back"
    assert rollback_payload["action"]["can_rollback"] is False
    assert rollback_payload["action"]["result"]["rollback"]["concepts"][0]["concept_id"] == concept_id

    with SessionLocal() as db:
        refreshed = db.query(ConceptMastery).filter(ConceptMastery.concept_id == concept_id).first()
        assert refreshed is not None
        assert round(float(refreshed.retention or 0.0), 4) == original_snapshot["retention"]
        assert round(float(refreshed.understanding or 0.0), 4) == original_snapshot["understanding"]
        assert round(float(refreshed.application or 0.0), 4) == original_snapshot["application"]
        assert (refreshed.last_tested.isoformat() if refreshed.last_tested else None) == original_snapshot["last_tested"]
        assert (refreshed.next_review.isoformat() if refreshed.next_review else None) == original_snapshot["next_review"]


def test_agent_action_create_daily_review_paper_replace_then_rollback():
    from learning_tracking_models import DailyReviewPaper, DailyReviewPaperItem, WrongAnswerV2
    from models import SessionLocal
    from services.data_identity import build_actor_key

    client = TestClient(app)
    device_id = f"agent-action-paper-rollback-{uuid4().hex}"
    first_id = _seed_scoped_wrong_answer(
        device_id=device_id,
        question_text="Rollback paper first",
        key_point="paper-kp-first",
    )
    second_id = _seed_scoped_wrong_answer(
        device_id=device_id,
        question_text="Rollback paper second",
        key_point="paper-kp-second",
    )
    paper_date = (date.today() + timedelta(days=19)).isoformat()

    with SessionLocal() as db:
        first_wrong_answer = db.query(WrongAnswerV2).filter(WrongAnswerV2.id == first_id).first()
        assert first_wrong_answer is not None
        paper = DailyReviewPaper(
            device_id=device_id,
            actor_key=build_actor_key(None, device_id),
            paper_date=date.fromisoformat(paper_date),
            total_questions=1,
            config={"target_count": 1, "source_breakdown": {"due": 1}},
        )
        paper.items.append(
            DailyReviewPaperItem(
                wrong_answer_id=first_id,
                position=1,
                stem_fingerprint=str(first_wrong_answer.question_fingerprint),
                source_bucket="due",
                snapshot={
                    "question_text": first_wrong_answer.question_text,
                    "key_point": first_wrong_answer.key_point,
                },
            )
        )
        db.add(paper)
        db.commit()
        db.refresh(paper)
        original_paper_id = int(paper.id)

    session_id = _create_agent_action_session(
        client,
        title="Daily review rollback session",
        device_id=device_id,
    )

    preview_response = _preview_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        tool_name="create_daily_review_paper",
        tool_args={
            "paper_date": paper_date,
            "wrong_answer_ids": [second_id],
            "target_count": 1,
            "allow_replace": True,
        },
    )
    assert preview_response.status_code == 200
    action_id = preview_response.json()["action"]["id"]

    confirm_response = _confirm_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["can_rollback"] is True

    with SessionLocal() as db:
        paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.device_id == device_id,
            )
            .first()
        )
        assert paper is not None
        assert int(paper.id) == original_paper_id
        assert [int(item.wrong_answer_id) for item in sorted(paper.items, key=lambda item: item.position)] == [second_id]

    rollback_response = _rollback_agent_action_request(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["action"]["execution_status"] == "rolled_back"
    assert rollback_payload["action"]["can_rollback"] is False

    with SessionLocal() as db:
        paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.device_id == device_id,
            )
            .first()
        )
        assert paper is not None
        assert int(paper.id) == original_paper_id
        assert [int(item.wrong_answer_id) for item in sorted(paper.items, key=lambda item: item.position)] == [first_id]
        assert paper.config["target_count"] == 1


def test_agent_action_generate_quiz_set_confirm_then_rollback():
    from learning_tracking_models import LearningSession, QuestionRecord
    from models import ConceptMastery, QuizSession, SessionLocal

    client = TestClient(app)
    device_id = f"agent-action-quiz-rollback-{uuid4().hex}"
    _seed_agent_learning_data(device_id)

    with SessionLocal() as db:
        concept = db.query(ConceptMastery).filter(ConceptMastery.device_id == device_id).first()
        assert concept is not None
        concept_id = concept.concept_id

    session_id = _create_agent_action_session(
        client,
        title="Quiz rollback session",
        device_id=device_id,
    )

    preview_response = _preview_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        tool_name="generate_quiz_set",
        tool_args={
            "concept_ids": [concept_id],
            "target_count": 4,
            "session_type": "practice",
            "title": "Rollback quiz set",
        },
    )
    assert preview_response.status_code == 200
    action_id = preview_response.json()["action"]["id"]

    confirm_response = _confirm_agent_action(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["action"]["execution_status"] == "success"
    assert confirm_payload["action"]["can_rollback"] is True
    result = confirm_payload["action"]["result"]

    rollback_response = _rollback_agent_action_request(
        client,
        session_id=session_id,
        device_id=device_id,
        action_id=action_id,
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["action"]["execution_status"] == "rolled_back"
    assert rollback_payload["action"]["can_rollback"] is False
    assert rollback_payload["action"]["result"]["rollback"]["quiz_session_id"] == result["quiz_session_id"]

    with SessionLocal() as db:
        quiz_session = db.query(QuizSession).filter(QuizSession.id == result["quiz_session_id"]).first()
        learning_session = db.query(LearningSession).filter(LearningSession.id == result["learning_session_id"]).first()
        question_record_count = db.query(QuestionRecord).filter(QuestionRecord.session_id == result["learning_session_id"]).count()

        assert quiz_session is None
        assert learning_session is None
        assert question_record_count == 0


def test_agent_action_create_daily_review_paper_accepts_user_only_scope():
    from learning_tracking_models import DailyReviewPaper
    from models import SessionLocal
    from services.data_identity import build_actor_key

    client = TestClient(app)
    user_id = f"agent-action-user-{uuid4().hex}"
    real_device_id = f"agent-action-user-device-{uuid4().hex}"
    wrong_answer_id = _seed_scoped_wrong_answer(
        user_id=user_id,
        device_id=real_device_id,
        question_text="User scoped daily review",
        key_point="user-scope-kp",
        next_review_offset_days=0,
    )

    session_response = client.post(
        "/api/agent/sessions",
        json={
            "user_id": user_id,
            "title": "User scoped session",
            "agent_type": "tutor",
        },
    )
    assert session_response.status_code == 200
    session_payload = session_response.json()
    session_id = session_payload["id"]
    paper_date = (date.today() + timedelta(days=18)).isoformat()

    preview_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "tool_name": "create_daily_review_paper",
            "tool_args": {
                "paper_date": paper_date,
                "target_count": 1,
            },
        },
    )
    assert preview_response.status_code == 200

    confirm_response = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_id,
            "user_id": user_id,
            "action_id": preview_response.json()["action"]["id"],
            "confirm": True,
        },
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["action"]["result"]["wrong_answer_ids"] == [wrong_answer_id]

    with SessionLocal() as db:
        paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.actor_key == build_actor_key(user_id, None),
            )
            .first()
        )
        assert paper is not None
        assert paper.user_id == user_id
        assert paper.device_id == f"user:{user_id}"


def test_agent_action_create_daily_review_paper_isolated_by_device():
    from learning_tracking_models import DailyReviewPaper, WrongAnswerV2
    from models import SessionLocal

    client = TestClient(app)
    device_a = f"agent-action-paper-a-{uuid4().hex}"
    device_b = f"agent-action-paper-b-{uuid4().hex}"
    _seed_agent_learning_data(device_a)
    _seed_agent_learning_data(device_b)

    with SessionLocal() as db:
        wrong_answer_a = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_a).first()
        wrong_answer_b = db.query(WrongAnswerV2).filter(WrongAnswerV2.device_id == device_b).first()
        assert wrong_answer_a is not None
        assert wrong_answer_b is not None
        wrong_answer_a_id = int(wrong_answer_a.id)
        wrong_answer_b_id = int(wrong_answer_b.id)

    session_a = client.post(
        "/api/agent/sessions",
        json={"device_id": device_a, "title": "Paper A", "agent_type": "tutor"},
    )
    session_b = client.post(
        "/api/agent/sessions",
        json={"device_id": device_b, "title": "Paper B", "agent_type": "tutor"},
    )
    assert session_a.status_code == 200
    assert session_b.status_code == 200
    session_a_id = session_a.json()["id"]
    session_b_id = session_b.json()["id"]
    paper_date = (date.today() + timedelta(days=21)).isoformat()

    preview_a = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_a_id,
            "device_id": device_a,
            "tool_name": "create_daily_review_paper",
            "tool_args": {
                "paper_date": paper_date,
                "wrong_answer_ids": [wrong_answer_a_id],
                "target_count": 1,
            },
        },
    )
    preview_b = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_b_id,
            "device_id": device_b,
            "tool_name": "create_daily_review_paper",
            "tool_args": {
                "paper_date": paper_date,
                "wrong_answer_ids": [wrong_answer_b_id],
                "target_count": 1,
            },
        },
    )
    assert preview_a.status_code == 200
    assert preview_b.status_code == 200

    confirm_a = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_a_id,
            "device_id": device_a,
            "action_id": preview_a.json()["action"]["id"],
            "confirm": True,
        },
    )
    confirm_b = client.post(
        "/api/agent/actions",
        json={
            "session_id": session_b_id,
            "device_id": device_b,
            "action_id": preview_b.json()["action"]["id"],
            "confirm": True,
        },
    )
    assert confirm_a.status_code == 200
    assert confirm_b.status_code == 200

    with SessionLocal() as db:
        paper_a = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.device_id == device_a,
            )
            .first()
        )
        paper_b = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.fromisoformat(paper_date),
                DailyReviewPaper.device_id == device_b,
            )
            .first()
        )
        assert paper_a is not None
        assert paper_b is not None
        assert paper_a.id != paper_b.id
        assert paper_a.actor_key != paper_b.actor_key
        assert [int(item.wrong_answer_id) for item in sorted(paper_a.items, key=lambda item: item.position)] == [wrong_answer_a_id]
        assert [int(item.wrong_answer_id) for item in sorted(paper_b.items, key=lambda item: item.position)] == [wrong_answer_b_id]


def test_daily_review_pdf_export_accepts_user_only_scope():
    from learning_tracking_models import DailyReviewPaper
    from models import SessionLocal
    from services.data_identity import build_actor_key

    client = TestClient(app)
    user_id = f"daily-review-user-{uuid4().hex}"
    real_device_id = f"daily-review-user-device-{uuid4().hex}"
    wrong_answer_id = _seed_scoped_wrong_answer(
        user_id=user_id,
        device_id=real_device_id,
        question_text="User only PDF export question",
        key_point="pdf-user-kp",
        next_review_offset_days=0,
    )

    response = client.get("/api/wrong-answers/daily-review-pdf", params={"user_id": user_id})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    with SessionLocal() as db:
        paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == date.today(),
                DailyReviewPaper.actor_key == build_actor_key(user_id, None),
            )
            .first()
        )
        assert paper is not None
        assert paper.user_id == user_id
        assert paper.device_id == f"user:{user_id}"
        assert [int(item.wrong_answer_id) for item in sorted(paper.items, key=lambda item: item.position)] == [wrong_answer_id]


def test_daily_review_pdf_export_falls_back_to_legacy_anonymous_actor_for_generated_device():
    from learning_tracking_models import DailyReviewPaper
    from models import SessionLocal
    from services.data_identity import DEFAULT_DEVICE_ID, build_actor_key

    client = TestClient(app)
    generated_device_id = f"local-{uuid4().hex}"
    target_date = date.today() + timedelta(days=37)
    wrong_answer_id = _seed_scoped_wrong_answer(
        device_id=DEFAULT_DEVICE_ID,
        question_text="Legacy anonymous PDF export question",
        key_point="pdf-legacy-anon-kp",
        next_review_offset_days=0,
    )

    response = client.get(
        "/api/wrong-answers/daily-review-pdf",
        params={"paper_date": target_date.isoformat()},
        headers={"X-TLS-Device-ID": generated_device_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    with SessionLocal() as db:
        legacy_paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == target_date,
                DailyReviewPaper.actor_key == build_actor_key(None, DEFAULT_DEVICE_ID),
            )
            .first()
        )
        generated_paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == target_date,
                DailyReviewPaper.actor_key == build_actor_key(None, generated_device_id),
            )
            .first()
        )

        assert legacy_paper is not None
        assert legacy_paper.device_id == DEFAULT_DEVICE_ID
        assert generated_paper is None
        selected_ids = [int(item.wrong_answer_id) for item in sorted(legacy_paper.items, key=lambda item: item.position)]
        assert wrong_answer_id in selected_ids


def test_daily_review_pdf_export_merges_legacy_pool_when_generated_device_has_current_data():
    from learning_tracking_models import DailyReviewPaper
    from models import SessionLocal
    from services.data_identity import DEFAULT_DEVICE_ID, build_actor_key

    client = TestClient(app)
    generated_device_id = f"local-{uuid4().hex}"
    target_date = date.today() + timedelta(days=38)
    current_wrong_answer_id = _seed_scoped_wrong_answer(
        device_id=generated_device_id,
        question_text="Generated device current PDF export question",
        key_point="pdf-generated-current-kp",
        next_review_offset_days=0,
    )
    legacy_wrong_answer_ids = [
        _seed_scoped_wrong_answer(
            device_id=DEFAULT_DEVICE_ID,
            question_text=f"Legacy anonymous pooled PDF export question {index}",
            key_point=f"pdf-legacy-merge-kp-{index}",
            next_review_offset_days=0,
        )
        for index in range(1, 3)
    ]

    response = client.get(
        "/api/wrong-answers/daily-review-pdf",
        params={"paper_date": target_date.isoformat()},
        headers={"X-TLS-Device-ID": generated_device_id},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    with SessionLocal() as db:
        current_paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == target_date,
                DailyReviewPaper.actor_key == build_actor_key(None, generated_device_id),
            )
            .first()
        )
        legacy_paper = (
            db.query(DailyReviewPaper)
            .filter(
                DailyReviewPaper.paper_date == target_date,
                DailyReviewPaper.actor_key == build_actor_key(None, DEFAULT_DEVICE_ID),
            )
            .first()
        )

        assert current_paper is not None
        assert current_paper.device_id == generated_device_id
        selected_ids = [int(item.wrong_answer_id) for item in sorted(current_paper.items, key=lambda item: item.position)]
        assert current_wrong_answer_id in selected_ids
        assert any(wrong_answer_id in selected_ids for wrong_answer_id in legacy_wrong_answer_ids)
        assert legacy_paper is None


def test_daily_review_pdf_export_reuses_legacy_user_only_paper_actor_key():
    from learning_tracking_models import DailyReviewPaper, DailyReviewPaperItem
    from models import SessionLocal

    client = TestClient(app)
    user_id = f"daily-review-legacy-user-{uuid4().hex}"
    real_device_id = f"daily-review-legacy-device-{uuid4().hex}"
    wrong_answer_id = _seed_scoped_wrong_answer(
        user_id=user_id,
        device_id=real_device_id,
        question_text="Legacy user only PDF export question",
        key_point="pdf-legacy-user-kp",
        next_review_offset_days=0,
    )
    target_date = date.today() + timedelta(days=2)
    legacy_actor_key = f"user:{user_id}|device:local-default"

    with SessionLocal() as db:
        paper = DailyReviewPaper(
            user_id=user_id,
            device_id="local-default",
            actor_key=legacy_actor_key,
            paper_date=target_date,
            total_questions=1,
            config={"legacy": True, "target_count": 1},
        )
        db.add(paper)
        db.flush()
        paper.items.append(
            DailyReviewPaperItem(
                wrong_answer_id=wrong_answer_id,
                position=1,
                stem_fingerprint=f"legacy-{uuid4().hex}",
                source_bucket="due",
                snapshot={"question_text": "Legacy question"},
            )
        )
        db.commit()

    response = client.get(
        "/api/wrong-answers/daily-review-pdf",
        params={"user_id": user_id, "paper_date": target_date.isoformat()},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    with SessionLocal() as db:
        papers = db.query(DailyReviewPaper).filter(DailyReviewPaper.paper_date == target_date).all()
        assert len(papers) == 1
        assert papers[0].actor_key == legacy_actor_key
