"""
日志系统维护测试
验证统一日志配置的完整性：文件创建、路由分发、轮转、级别控制。
运行: python -m pytest test_logging_system.py -v
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_log_dir(tmp_path, monkeypatch):
    """每个测试用例使用独立的临时日志目录，避免污染生产日志。"""
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_MAX_BYTES", "1024")  # 1KB — 方便测试轮转
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")
    # 清除 root logger 旧 handler，确保 setup_logging 重新初始化
    root = logging.getLogger()
    root.handlers.clear()
    yield tmp_path / "logs"


def _setup():
    from utils.logging_config import setup_logging
    setup_logging()


# ─── Test 1: 日志目录和文件创建 ───

def test_log_directory_and_files_created(_isolate_log_dir):
    """setup_logging() 应创建 data/logs/ 及所有分模块日志文件。"""
    log_dir = _isolate_log_dir
    _setup()

    assert log_dir.exists(), "日志目录未创建"

    expected = ["app.log", "quiz.log", "agent.log", "ai_client.log", "upload.log", "tracking.log"]
    for name in expected:
        assert (log_dir / name).exists(), f"{name} 未创建"


# ─── Test 2: 日志路由正确性 ───

_ROUTING_CASES = [
    ("routers.quiz_batch", "quiz.log", "QUIZ_ROUTE_TEST"),
    ("services.quiz_service_v2", "quiz.log", "QUIZ_SVC_TEST"),
    ("services.agent_runtime", "agent.log", "AGENT_TEST"),
    ("routers.agent", "agent.log", "AGENT_ROUTER_TEST"),
    ("services.ai_client", "ai_client.log", "AI_CLIENT_TEST"),
    ("routers.upload", "upload.log", "UPLOAD_TEST"),
    ("services.content_parser_v2", "upload.log", "PARSER_TEST"),
    ("routers.learning_tracking", "tracking.log", "TRACKING_TEST"),
    ("routers.wrong_answers_v2", "tracking.log", "WRONG_ANS_TEST"),
    ("routers.challenge", "tracking.log", "CHALLENGE_TEST"),
]


@pytest.mark.parametrize("logger_name,target_file,marker", _ROUTING_CASES)
def test_log_routing(logger_name, target_file, marker, _isolate_log_dir):
    """各模块日志应同时写入 app.log 和对应的分模块日志文件。"""
    log_dir = _isolate_log_dir
    _setup()

    logger = logging.getLogger(logger_name)
    logger.info("MARKER_%s", marker)

    # flush all handlers
    for handler in logging.getLogger().handlers:
        handler.flush()
    for handler in logger.handlers:
        handler.flush()

    # 验证 app.log 包含该条目
    app_content = (log_dir / "app.log").read_text(encoding="utf-8")
    assert f"MARKER_{marker}" in app_content, f"app.log 中未找到 MARKER_{marker}"

    # 验证分模块日志包含该条目
    module_content = (log_dir / target_file).read_text(encoding="utf-8")
    assert f"MARKER_{marker}" in module_content, f"{target_file} 中未找到 MARKER_{marker}"


# ─── Test 3: 日志不串台 ───

def test_log_isolation(_isolate_log_dir):
    """quiz 日志不应出现在 agent.log 中，反之亦然。"""
    log_dir = _isolate_log_dir
    _setup()

    quiz_logger = logging.getLogger("routers.quiz_batch")
    agent_logger = logging.getLogger("services.agent_runtime")

    quiz_logger.info("ISOLATION_QUIZ_ONLY")
    agent_logger.info("ISOLATION_AGENT_ONLY")

    for h in logging.getLogger().handlers:
        h.flush()
    for h in quiz_logger.handlers:
        h.flush()
    for h in agent_logger.handlers:
        h.flush()

    agent_content = (log_dir / "agent.log").read_text(encoding="utf-8")
    quiz_content = (log_dir / "quiz.log").read_text(encoding="utf-8")

    assert "ISOLATION_QUIZ_ONLY" not in agent_content, "quiz 日志泄漏到 agent.log"
    assert "ISOLATION_AGENT_ONLY" not in quiz_content, "agent 日志泄漏到 quiz.log"


# ─── Test 4: 日志级别控制 ───

def test_log_level_filtering(_isolate_log_dir, monkeypatch):
    """LOG_LEVEL=WARNING 时，INFO 级别日志不应写入文件。"""
    log_dir = _isolate_log_dir
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    _setup()

    logger = logging.getLogger("routers.quiz_batch")
    logger.info("SHOULD_NOT_APPEAR")
    logger.warning("SHOULD_APPEAR")

    for h in logging.getLogger().handlers:
        h.flush()
    for h in logger.handlers:
        h.flush()

    content = (log_dir / "quiz.log").read_text(encoding="utf-8")
    assert "SHOULD_NOT_APPEAR" not in content
    assert "SHOULD_APPEAR" in content


# ─── Test 5: 日志格式验证 ───

def test_log_format(_isolate_log_dir):
    """日志格式应为: 时间 [级别] 模块名: 消息"""
    import re
    log_dir = _isolate_log_dir
    _setup()

    logger = logging.getLogger("services.ai_client")
    logger.info("FORMAT_CHECK_MSG")

    for h in logging.getLogger().handlers:
        h.flush()
    for h in logger.handlers:
        h.flush()

    content = (log_dir / "ai_client.log").read_text(encoding="utf-8")
    # 匹配: 2026-03-20 14:05:58 [INFO] services.ai_client: FORMAT_CHECK_MSG
    pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[INFO\] services\.ai_client: FORMAT_CHECK_MSG"
    assert re.search(pattern, content), f"日志格式不符合预期，实际内容:\n{content}"


# ─── Test 6: 日志轮转 ───

def test_log_rotation(_isolate_log_dir):
    """超过 LOG_MAX_BYTES 后应自动轮转，生成 .1 .2 备份文件。"""
    log_dir = _isolate_log_dir
    _setup()

    logger = logging.getLogger("routers.quiz_batch")
    # 写入足够多的日志触发轮转 (LOG_MAX_BYTES=1024)
    for i in range(200):
        logger.info("ROTATION_TEST_%04d padding_to_fill_up_the_log_file_quickly", i)

    for h in logging.getLogger().handlers:
        h.flush()
    for h in logger.handlers:
        h.flush()

    quiz_log = log_dir / "quiz.log"
    quiz_log_1 = log_dir / "quiz.log.1"

    assert quiz_log.exists(), "quiz.log 不存在"
    assert quiz_log_1.exists(), "quiz.log.1 未生成，轮转未触发"


# ─── Test 7: 第三方库噪音抑制 ───

def test_noisy_loggers_suppressed(_isolate_log_dir):
    """httpx/openai 等第三方库应被设为 WARNING 级别。"""
    _setup()

    for name in ("httpx", "httpcore", "openai", "uvicorn.access"):
        assert logging.getLogger(name).level >= logging.WARNING, \
            f"{name} 日志级别未被抑制"


# ─── Test 8: 重复调用 setup_logging 不会叠加 handler ───

def test_no_duplicate_handlers(_isolate_log_dir):
    """多次调用 setup_logging() 不应导致 handler 重复叠加。"""
    _setup()
    handler_count_1 = len(logging.getLogger().handlers)

    _setup()
    handler_count_2 = len(logging.getLogger().handlers)

    assert handler_count_2 == handler_count_1, \
        f"handler 数量从 {handler_count_1} 增长到 {handler_count_2}，存在重复叠加"


# ─── Test 9: 模块导入不触发 basicConfig ───

def test_no_basic_config_in_modules():
    """ai_client.py 和 quiz_service_v2.py 不应包含 logging.basicConfig 调用。"""
    for filepath in [
        "services/ai_client.py",
        "services/quiz_service_v2.py",
    ]:
        content = Path(filepath).read_text(encoding="utf-8")
        assert "basicConfig" not in content, \
            f"{filepath} 仍包含 logging.basicConfig，应使用统一配置"


# ─── Test 10: Session 生命周期日志标签存在 ───

_SESSION_LOG_MARKERS = [
    ("services/agent_runtime.py", "[AGENT_SESSION]"),
    ("routers/quiz_batch.py", "[QUIZ_SESSION]"),
    ("routers/learning_tracking.py", "[LEARNING_SESSION]"),
]


@pytest.mark.parametrize("filepath,marker", _SESSION_LOG_MARKERS)
def test_session_lifecycle_markers(filepath, marker):
    """关键模块应包含结构化 Session 生命周期日志标签。"""
    content = Path(filepath).read_text(encoding="utf-8")
    assert marker in content, f"{filepath} 中未找到 {marker} 生命周期日志标签"
