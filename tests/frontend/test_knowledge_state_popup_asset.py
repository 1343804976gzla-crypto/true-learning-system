from pathlib import Path


def test_knowledge_state_popup_asset_exists_and_exports_expected_hooks():
    asset_path = Path("static/js/knowledge-state-popup.js")

    assert asset_path.exists()
    content = asset_path.read_text(encoding="utf-8")

    assert "window.showKnowledgeStateAfterPractice" in content
    assert "/api/tracking/knowledge-state/analyze" in content
    assert "knowledge-state-modal" in content
    assert "state_confidence" in content
    assert ".next_action.label" in content or "nextAction.label" in content
    assert "[object Object]" not in content
