from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditLogConfig:
    data_dir: Path
    retention_days: int = 30


class AuditLog:
    def __init__(self, config: AuditLogConfig) -> None:
        self.config = config
        self.db_path = config.data_dir / "audit.sqlite3"

    def initialize(self) -> None:
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    target TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_command_audit_created ON command_audit(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_command_audit_group ON command_audit(group_id, id)")
        self.cleanup_old()

    def record_event(
        self,
        *,
        group_id: str,
        user_id: str,
        command: str,
        target: str | None,
        status: str,
        error_code: str | None = None,
        duration_ms: int = 0,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        values = {
            "created_at": now,
            "group_id": _limit(group_id, 32),
            "user_id": _limit(user_id, 32),
            "command": _limit(command, 64),
            "target": _limit(target, 128) if target else None,
            "status": _limit(status, 32),
            "error_code": _limit(error_code, 64) if error_code else None,
            "duration_ms": max(0, int(duration_ms)),
        }
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO command_audit (
                    created_at, group_id, user_id, command, target, status, error_code, duration_ms
                ) VALUES (
                    :created_at, :group_id, :user_id, :command, :target, :status, :error_code, :duration_ms
                )
                """,
                values,
            )
            values["id"] = cursor.lastrowid
        return values

    def list_events(self, *, group_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        params: list[Any] = []
        where = ""
        if group_id:
            where = "WHERE group_id = ?"
            params.append(group_id)
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, created_at, group_id, user_id, command, target, status, error_code, duration_ms
                FROM command_audit
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup_old(self) -> int:
        if self.config.retention_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM command_audit WHERE created_at < ?",
                (cutoff.isoformat(timespec="seconds"),),
            )
            return int(cursor.rowcount or 0)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


def _limit(value: str | None, max_length: int) -> str:
    return str(value or "")[:max_length]
