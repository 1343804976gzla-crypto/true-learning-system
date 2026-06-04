from pathlib import Path


def test_quiz_detail_loads_knowledge_state_popup_and_invokes_detail_session():
    content = Path("templates/quiz_detail.html").read_text(encoding="utf-8")

    assert "/static/js/knowledge-state-popup.js" in content
    assert "showKnowledgeStateAfterPractice(detailTrackingSessionId" in content


def test_exam_loads_knowledge_state_popup_and_invokes_tracking_session():
    content = Path("templates/exam.html").read_text(encoding="utf-8")

    assert "/static/js/knowledge-state-popup.js" in content
    assert "showKnowledgeStateAfterPractice(trackingSessionId" in content


def test_quiz_batch_loads_knowledge_state_popup_and_invokes_tracking_session():
    content = Path("templates/quiz_batch.html").read_text(encoding="utf-8")

    assert "/static/js/knowledge-state-popup.js" in content
    assert "showKnowledgeStateAfterPractice(trackingSessionId" in content
