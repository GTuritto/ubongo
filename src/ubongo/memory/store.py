from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("ubongo.memory.store")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB_PATH = _REPO_ROOT / "data" / "ubongo.db"
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_connection: sqlite3.Connection | None = None
_bootstrapped = False


def get_db_path() -> Path:
    return _DB_PATH


def set_db_path(path: Path) -> None:
    """Override the DB path (used by tests with tempfiles)."""
    global _DB_PATH, _connection, _bootstrapped
    _DB_PATH = path
    if _connection is not None:
        _connection.close()
    _connection = None
    _bootstrapped = False


def _now() -> datetime:
    fake = os.environ.get("UBONGO_FAKE_NOW")
    if fake:
        return datetime.fromisoformat(fake)
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return _now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ensure_dir() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def bootstrap() -> sqlite3.Connection:
    global _connection, _bootstrapped
    if _connection is None:
        _ensure_dir()
        _connection = sqlite3.connect(_DB_PATH, isolation_level=None)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA foreign_keys = ON")
    if not _bootstrapped:
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        _connection.executescript(schema_sql)
        _bootstrapped = True
    return _connection


def connection() -> sqlite3.Connection:
    return bootstrap()
