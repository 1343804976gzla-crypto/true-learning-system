from pathlib import Path


def test_quiz_detail_loads_knowledge_state_popup_and_invokes_detail_session():
    content = Path("templates/quiz_detail.html").read_text(encoding="utf-8")
    completion_function = content[
        content.index("async function completeDetailTrackingSession"):
        content.index("const originalCompleteDetailTrackingSession")
    ]
    wrapper = content[
        content.index("const originalCompleteDetailTrackingSession"):
        content.index("async function generateVariationQuestions")
    ]

    assert "/static/js/knowledge-state-popup.js" in content
    assert "showKnowledgeStateAfterPractice(detailTrackingSessionId" in content
    assert "return true;" in completion_function
    assert completion_function.count("return false;") >= 3
    assert "const trackingCompleted = await originalCompleteDetailTrackingSession(score, totalQuestions);" in wrapper
    assert "trackingCompleted &&" in wrapper
    assert "try {" in wrapper
    assert "catch (error)" in wrapper
    assert wrapper.index("catch (error)") < wrapper.index("bumpKnowledgePracticeCount(currentKnowledge)")


def test_exam_loads_knowledge_state_popup_and_invokes_tracking_session():
    content = Path("templates/exam.html").read_text(encoding="utf-8")

    assert "/static/js/knowledge-state-popup.js" in content
    assert "showKnowledgeStateAfterPractice(trackingSessionId" in content


def test_quiz_batch_loads_knowledge_state_popup_and_invokes_tracking_session():
    content = Path("templates/quiz_batch.html").read_text(encoding="utf-8")
    complete_session = content[
        content.index("async function completeTrackingSession"):
        content.index("// 全局变量：保存最后一次结果")
    ]

    assert "/static/js/knowledge-state-popup.js" in content
    assert "showKnowledgeStateAfterPractice(trackingSessionId" in content
    assert "const response = await fetch('/api/tracking/session/' + trackingSessionId + '/complete'" in complete_session
    assert "if (!response.ok)" in complete_session
    assert complete_session.index("if (!response.ok)") < complete_session.index("showKnowledgeStateAfterPractice(trackingSessionId")
