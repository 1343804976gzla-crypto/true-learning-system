from fastapi.testclient import TestClient

from main import app, _format_dashboard_accuracy


client = TestClient(app)


def test_dashboard_accuracy_formatter_handles_ratio_and_percent_values():
    assert _format_dashboard_accuracy(0.64) == 64.0
    assert _format_dashboard_accuracy(64.0) == 64.0
    assert _format_dashboard_accuracy(None) is None


def test_history_stats_exposes_streak_days():
    response = client.get("/api/history/stats")

    assert response.status_code == 200
    payload = response.json()
    assert "streak_days" in payload
    assert isinstance(payload["streak_days"], int)


def test_dashboard_page_renders_live_launchpad_metrics():
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "累计整卷" in html
    assert "今日到期" in html
    assert "本周新增" in html
    assert "连续学习" in html
    assert "dashboard-bento-card__note" in html
    assert "dashboard-bento-card__metric-value" in html
