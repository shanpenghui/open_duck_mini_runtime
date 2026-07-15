from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "assistant.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class ExecuteResult:
    lastrowid: int | None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_db_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = get_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(db_path: str | Path | None = None) -> None:
    connection = connect(db_path)
    try:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


def execute(
    sql: str,
    params: Iterable[Any] = (),
    *,
    db_path: str | Path | None = None,
) -> ExecuteResult:
    initialize(db_path)
    connection = connect(db_path)
    try:
        cursor = connection.execute(sql, tuple(params))
        lastrowid = cursor.lastrowid
        cursor.close()
        connection.commit()
        return ExecuteResult(lastrowid=lastrowid)
    finally:
        connection.close()


def fetch_all(
    sql: str,
    params: Iterable[Any] = (),
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    initialize(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def fetch_one(
    sql: str,
    params: Iterable[Any] = (),
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    rows = fetch_all(sql, params, db_path=db_path)
    return rows[0] if rows else None
