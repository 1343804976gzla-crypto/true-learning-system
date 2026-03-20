"""
统一日志配置模块
集中管理所有模块的日志格式、级别、文件轮转。
在 main.py startup 时调用一次 setup_logging() 即可。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 分模块日志 → 对应的 logger name 前缀
_MODULE_LOG_FILES = {
    "quiz.log": [
        "services.quiz_service",
        "services.quiz_service_v2",
        "routers.quiz",
        "routers.quiz_batch",
        "routers.quiz_batch_append",
        "routers.quiz_fast",
        "routers.quiz_concurrent",
        "routers.quiz_variations",
        "services.variation_service",
        "services.variant_surgery_service",
        "services.concurrent_quiz",
        "services.pre_generated_quiz",
    ],
    "agent.log": [
        "services.agent_runtime",
        "services.agent_actions",
        "services.agent_tools",
        "services.agent_context",
        "services.agent_memory",
        "services.agent_tasks",
        "routers.agent",
    ],
    "ai_client.log": [
        "services.ai_client",
        "services.ai_client_v2",
    ],
    "upload.log": [
        "routers.upload",
        "services.content_parser",
        "services.content_parser_v2",
        "services.knowledge_upload_service",
    ],
    "tracking.log": [
        "routers.learning_tracking",
        "routers.wrong_answers_v2",
        "routers.challenge",
        "routers.dashboard",
        "services.chapter_review_service",
    ],
}


def setup_logging() -> None:
    """初始化全局日志配置。应在 app startup 时调用一次。"""
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_dir = Path(os.getenv("LOG_DIR", "./data/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = int(os.getenv("LOG_MAX_BYTES", 10 * 1024 * 1024))  # 10MB
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", 5))

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    # ── root logger: console + app.log ──
    root = logging.getLogger()
    root.setLevel(log_level)

    # 清除可能被 basicConfig 添加的旧 handler
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    app_handler = RotatingFileHandler(
        str(log_dir / "app.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    app_handler.setLevel(log_level)
    app_handler.setFormatter(formatter)
    root.addHandler(app_handler)

    # ── 分模块日志文件 ──
    for filename, logger_names in _MODULE_LOG_FILES.items():
        file_handler = RotatingFileHandler(
            str(log_dir / filename),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)

        for name in logger_names:
            module_logger = logging.getLogger(name)
            module_logger.addHandler(file_handler)

    # 降低第三方库噪音
    for noisy in ("httpx", "httpcore", "openai", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
