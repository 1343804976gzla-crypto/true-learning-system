from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _normalize_database_url(value: str, *, default_relative_path: str | None = None) -> str:
    candidate = (value or "").strip()
    if not candidate and default_relative_path:
        candidate = default_relative_path

    if candidate.startswith("sqlite:///"):
        return candidate

    if not candidate:
        raise ValueError("database path is required")

    db_path = Path(candidate)
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path.as_posix()}"


def _resolve_database_url(*env_names: str, default_relative_path: str) -> str:
    for env_name in env_names:
        value = (os.getenv(env_name) or "").strip()
        if value:
            return _normalize_database_url(value)
    return _normalize_database_url("", default_relative_path=default_relative_path)


def get_sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    return Path(database_url.replace("sqlite:///", "", 1)).resolve()


def _create_sqlite_engine(database_url: str) -> Engine:
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout = 30000")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.close()
        except Exception:
            pass

    return engine


CORE_DATABASE_URL = _resolve_database_url(
    "DATABASE_PATH",
    "CORE_DATABASE_PATH",
    "CORE_DATABASE_URL",
    default_relative_path="data/learning.db",
)
CONTENT_DATABASE_URL = _normalize_database_url(
    os.getenv("CONTENT_DATABASE_PATH") or os.getenv("CONTENT_DATABASE_URL") or CORE_DATABASE_URL
)
LEGACY_DATABASE_URL = _normalize_database_url(
    os.getenv("LEGACY_DATABASE_PATH") or os.getenv("LEGACY_DATABASE_URL") or CORE_DATABASE_URL
)

AGENT_DATABASE_URL = _normalize_database_url(
    os.getenv("AGENT_DATABASE_PATH") or os.getenv("AGENT_DATABASE_URL") or CORE_DATABASE_URL
)
RUNTIME_DATABASE_URL = _normalize_database_url(
    os.getenv("RUNTIME_DATABASE_PATH") or os.getenv("RUNTIME_DATABASE_URL") or CORE_DATABASE_URL
)
REVIEW_DATABASE_URL = _normalize_database_url(
    os.getenv("REVIEW_DATABASE_PATH") or os.getenv("REVIEW_DATABASE_URL") or CORE_DATABASE_URL
)

CoreBase = declarative_base()
ContentBase = declarative_base()
LegacyBase = declarative_base()
AgentBase = declarative_base()
RuntimeBase = declarative_base()
ReviewBase = declarative_base()

core_engine = _create_sqlite_engine(CORE_DATABASE_URL)
content_engine = _create_sqlite_engine(CONTENT_DATABASE_URL)
legacy_engine = _create_sqlite_engine(LEGACY_DATABASE_URL)
agent_engine = _create_sqlite_engine(AGENT_DATABASE_URL)
runtime_engine = _create_sqlite_engine(RUNTIME_DATABASE_URL)
review_engine = _create_sqlite_engine(REVIEW_DATABASE_URL)

CoreSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=core_engine)
ContentSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=content_engine)
LegacySessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=legacy_engine)
AgentSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=agent_engine)
RuntimeSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=runtime_engine)
ReviewSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=review_engine)


class RoutedDomainSession(Session):
    """Route ORM models to their owning domain database."""

    def get_bind(self, mapper=None, clause=None, **kwargs):
        if mapper is not None:
            table = getattr(mapper.class_, "__table__", None)
            metadata = getattr(table, "metadata", None)
            if metadata is ContentBase.metadata:
                return content_engine
            if metadata is LegacyBase.metadata:
                return legacy_engine
            if metadata is AgentBase.metadata:
                return agent_engine
            if metadata is RuntimeBase.metadata:
                return runtime_engine
            if metadata is ReviewBase.metadata:
                return review_engine
            if metadata is CoreBase.metadata:
                return core_engine

        froms = getattr(clause, "froms", None) or []
        for from_clause in froms:
            metadata = getattr(from_clause, "metadata", None)
            if metadata is ContentBase.metadata:
                return content_engine
            if metadata is LegacyBase.metadata:
                return legacy_engine
            if metadata is AgentBase.metadata:
                return agent_engine
            if metadata is RuntimeBase.metadata:
                return runtime_engine
            if metadata is ReviewBase.metadata:
                return review_engine
            if metadata is CoreBase.metadata:
                return core_engine

        return core_engine


AppSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    class_=RoutedDomainSession,
)


def _session_dependency(factory: sessionmaker) -> Iterator[Session]:
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    yield from _session_dependency(AppSessionLocal)


def get_core_db() -> Iterator[Session]:
    yield from _session_dependency(CoreSessionLocal)


def get_content_db() -> Iterator[Session]:
    yield from _session_dependency(AppSessionLocal)


def get_legacy_db() -> Iterator[Session]:
    yield from _session_dependency(AppSessionLocal)


def get_agent_db() -> Iterator[Session]:
    yield from _session_dependency(AppSessionLocal)


def get_review_db() -> Iterator[Session]:
    yield from _session_dependency(AppSessionLocal)


def get_runtime_db() -> Iterator[Session]:
    yield from _session_dependency(AppSessionLocal)
