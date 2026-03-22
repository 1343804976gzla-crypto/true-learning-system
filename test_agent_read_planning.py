from __future__ import annotations

import asyncio

from services import agent_runtime, agent_tools


def test_build_request_analysis_includes_intent_scoped_read_plan():
    analysis = agent_runtime.build_request_analysis(
        "结合我的上传历史、未来趋势和今天的复习计划，帮我拆解接下来的安排",
        ["get_progress_summary", "get_review_pressure", "get_wrong_answers", "get_study_history"],
    )

    assert analysis["read_strategy"] == "intent_scoped"
    assert analysis["read_plan"]["mode"] == "intent_scoped"
    assert analysis["read_plan"]["read_full_database"] is False

    tools = {item["tool_name"]: item for item in analysis["read_plan"]["tools"]}
    assert tools["get_progress_summary"]["filters"]["period"] == "30d"
    assert tools["get_wrong_answers"]["filters"]["limit"] == 4
    assert tools["get_study_history"]["filters"]["days"] == 30
    assert tools["get_review_pressure"]["filters"]["daily_planned_review"] == 20


def test_extract_topic_hint_from_message_without_database_scan():
    topic = agent_runtime._extract_topic_hint("我最近是不是专题细胞电活动学得不太好")

    assert topic == "专题细胞电活动"


def test_attach_runtime_context_to_tool_overrides_adds_internal_runtime_block():
    request_analysis = agent_runtime.build_request_analysis(
        "帮我看下最近的错题和复习压力",
        ["get_wrong_answers", "get_review_pressure"],
    )

    overrides = agent_runtime._attach_runtime_context_to_tool_overrides(
        {
            "get_wrong_answers": {"status": "active", "limit": 4},
        },
        request_analysis,
        ["get_wrong_answers", "get_review_pressure"],
    )

    assert overrides["get_wrong_answers"]["status"] == "active"
    assert overrides["get_wrong_answers"]["__runtime"]["output_mode"] == request_analysis["output_mode"]
    assert overrides["get_wrong_answers"]["__runtime"]["focuses"]
    assert overrides["get_review_pressure"]["__runtime"]["reason"]


def test_execute_agent_tool_attaches_standard_read_contract(monkeypatch):
    async def _fake_run_wrong_answers(
        db,
        overrides,
        *,
        user_id=None,
        device_id=None,
        runtime_context=None,
    ):
        del db, user_id, device_id
        assert runtime_context["output_mode"] == "plan"
        tool_args = {"status": "active", "limit": 4}
        payload = agent_tools._attach_standard_read_format(
            "get_wrong_answers",
            tool_args,
            {
                "count": 9,
                "returned_count": 4,
                "items": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}],
            },
            runtime_context or {},
            read_mode="targeted_list",
            source_tables=["wrong_answers_v2"],
            selected_fields=["id"],
            sort=["updated_at desc"],
            total_count=9,
            returned_count=4,
        )
        return tool_args, payload

    monkeypatch.setattr(agent_tools, "_run_wrong_answers", _fake_run_wrong_answers)

    _, result, duration_ms = asyncio.run(
        agent_tools.execute_agent_tool(
            "get_wrong_answers",
            db=None,
            overrides={
                "limit": 4,
                "__runtime": {
                    "goal": "生成一个可执行复习方案",
                    "output_mode": "plan",
                    "time_horizon": "今天",
                    "message_excerpt": "帮我看错题",
                    "focuses": [{"id": "weakness_review", "title": "定位高风险错题"}],
                    "reason": "先看错题证据",
                },
            },
        )
    )

    assert duration_ms >= 0
    assert result["tool_name"] == "get_wrong_answers"
    assert result["read_contract"]["version"] == "db-read.v1"
    assert result["read_contract"]["read_full_database"] is False
    assert result["read_contract"]["intent"]["output_mode"] == "plan"
    assert result["read_contract"]["filters"]["status"] == "active"
    assert result["result_stats"]["total_count"] == 9
    assert result["result_stats"]["returned_count"] == 4
    assert result["result_stats"]["sampled"] is True
