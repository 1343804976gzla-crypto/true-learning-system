from pathlib import Path


TEMPLATES_DIR = Path(r"C:\Users\35456\true-learning-system\templates")


def read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def test_navigation_and_entry_points_hide_graph_and_dashboards():
    base_html = read_template("base.html")
    dashboard_html = read_template("dashboard.html")
    chapter_html = read_template("chapter.html")
    wrong_answers_html = read_template("wrong_answers.html")
    test_html = read_template("test.html")

    assert '/progress-board' not in base_html
    assert '/graph' not in base_html
    assert '进度看板' not in base_html
    assert '知识图谱' not in base_html

    assert '/graph' not in dashboard_html
    assert '知识图谱' not in dashboard_html

    assert '/graph' not in chapter_html
    assert '查看知识图谱' not in chapter_html

    assert '/dashboard/stats' not in wrong_answers_html
    assert 'Wrong Answer Dashboard' in wrong_answers_html
    assert 'dashboardActiveCount' in wrong_answers_html
    assert 'dashboardTrendSection' in wrong_answers_html
    assert 'dashboardWeakChaptersBody' in wrong_answers_html
    assert '/api/wrong-answers/dashboard' in wrong_answers_html

    assert '/graph' not in test_html


def test_retired_pages_render_rebuild_placeholder_content():
    graph_html = read_template("graph.html")
    progress_board_html = read_template("progress_board.html")
    dashboard_stats_html = read_template("dashboard_stats.html")

    assert '知识图谱正在重构' in graph_html
    assert 'Feature Offline' in graph_html

    assert '进度看板正在重构' in progress_board_html
    assert 'Feature Offline' in progress_board_html

    assert '数据看板正在重构' in dashboard_stats_html
    assert 'Feature Offline' in dashboard_stats_html
