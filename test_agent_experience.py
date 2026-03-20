from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from services.agent_prompt_templates import resolve_prompt_template
from services.agent_runtime import _shorten


def _read_agent_template() -> str:
    template_path = Path(__file__).resolve().parent / "templates" / "agent.html"
    return template_path.read_text(encoding="utf-8")


def _read_base_template() -> str:
    template_path = Path(__file__).resolve().parent / "templates" / "base.html"
    return template_path.read_text(encoding="utf-8")


def test_tutor_prompt_template_prefers_natural_dialogue():
    template_id, template = resolve_prompt_template("tutor", "tutor.v2")

    assert template_id == "tutor.v2"
    assert "自然对话" in template
    assert "一两段" in template
    assert "固定标题" in template
    assert "并肩分析" in template


def test_agent_template_uses_enter_to_send_and_shift_enter_for_newline():
    template = _read_agent_template()

    assert "Enter 发送，Shift+Enter 换行" in template
    assert "event.isComposing" in template
    assert "event.keyCode === 229" in template
    assert "const isEnter = event.key === 'Enter' || event.code === 'NumpadEnter';" in template
    assert "if (!isEnter || event.shiftKey) return;" in template


def test_agent_template_renders_assistant_markdown_instead_of_raw_tokens():
    template = _read_agent_template()

    assert "function renderMarkdown(value)" in template
    assert "function setMessageBody(body, role, content)" in template
    assert "body.innerHTML = renderMarkdown(text);" in template
    assert "async function revealAssistantDelta(chunk, { forceChunked = false } = {})" in template
    assert "await revealAssistantDelta(payload.content || '');" in template


def test_agent_template_keeps_composer_visible_and_animates_non_stream_chunks():
    template = _read_agent_template()

    assert "document.body.classList.add('tls-agent-page');" in template
    assert "body.tls-agent-page main.max-w-6xl {" in template
    assert "body.tls-agent-page footer {" in template
    assert ".agent-shell.has-messages .agent-composer {" in template
    assert "position: sticky;" in template
    assert "padding-bottom: 2.4rem;" in template
    assert "await revealAssistantDelta(fallbackText.slice(currentText.length), { forceChunked: true });" in template


def test_agent_template_disables_idle_motion_that_causes_visual_flicker():
    template = _read_agent_template()

    assert ".agent-background-svg path {" in template
    assert "stroke-dasharray: 96 184;" in template
    assert "stroke-dashoffset: -120;" in template
    assert "animation: agentPathDrift var(--duration, 24s) linear infinite;" not in template
    assert ".agent-send-button:not(.is-empty):not(:disabled)::before {" in template
    assert ".agent-send-button:not(.is-empty):not(:disabled) .agent-send-arrow {" in template


def test_agent_template_is_reduced_to_history_and_chat_only():
    template = _read_agent_template()

    assert 'id="agentHistoryPanel"' in template
    assert 'id="agentHistoryList"' in template
    assert 'id="agentStream"' in template
    assert 'id="agentComposer"' in template
    assert 'id="agentInput"' in template
    assert "agent-source-card" not in template
    assert "agentTaskBoard" not in template
    assert "action_suggestions" not in template
    assert "/api/agent/actions" not in template
    assert "/api/agent/tasks" not in template


def test_agent_template_supports_collapsible_history_panel():
    template = _read_agent_template()

    assert "HISTORY_COLLAPSE_KEY" in template
    assert "function setHistoryCollapsed(collapsed, persist = true)" in template
    assert "function setHistoryDrawerOpen(open)" in template
    assert "is-history-collapsed" in template
    assert "grid-template-columns: minmax(0, 1fr);" in template
    assert ".agent-shell.is-history-collapsed .agent-history-panel {" in template
    assert "display: none;" in template
    assert 'id="agentHistoryToggle"' in template
    assert 'id="agentHistoryClose"' in template


def test_base_template_generates_unique_anonymous_device_ids():
    template = _read_base_template()

    assert "var legacyDefaultDeviceId = 'local-default';" in template
    assert "function createDeviceId()" in template
    assert "if (!value || value === legacyDefaultDeviceId)" in template
    assert "return createDeviceId();" in template


def test_agent_template_restores_drafts_and_renders_retry_states():
    template = _read_agent_template()

    assert "const DRAFT_STORAGE_PREFIX = 'tls-agent-draft';" in template
    assert "function persistDraft()" in template
    assert "function restoreDraft()" in template
    assert 'data-agent-retry="sessions"' in template
    assert 'data-agent-retry="messages"' in template
    assert "state.sessionsLoading = true;" in template
    assert "state.messagesLoading = true;" in template


def test_agent_template_recovers_failed_send_and_guards_message_races():
    template = _read_agent_template()

    assert "const loadId = ++state.messageLoadVersion;" in template
    assert "if (loadId !== state.messageLoadVersion || sessionId !== state.currentSessionId) return false;" in template
    assert "const draftValue = el.input.value;" in template
    assert "el.input.value = draftValue;" in template
    assert "await loadSessions(state.currentSessionId, { preserveStatus: true });" in template


def test_agent_template_optimistically_renders_user_messages_while_streaming():
    template = _read_agent_template()

    assert "function insertOptimisticUserMessage(content, traceId)" in template
    assert "String(item.id || '').startsWith('temp-user-')" in template
    assert "const optimisticUserMessageId = insertOptimisticUserMessage(message, clientRequestId);" in template
    assert "removeMessageById(optimisticUserMessageId);" in template


def test_agent_page_renders_minimal_shell():
    client = TestClient(app)

    response = client.get("/agent")

    assert response.status_code == 200
    assert 'id="agentHistoryPanel"' in response.text
    assert 'id="agentStream"' in response.text
    assert 'id="agentComposer"' in response.text


def test_shorten_strips_markdown_tokens_from_previews():
    assert _shorten("**重点** 内容") == "重点 内容"
    assert _shorten("1. **第一条**", limit=20) == "第一条"
