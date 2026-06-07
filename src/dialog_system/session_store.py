"""SQLite storage for dialog sessions with logging and metrics."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

from .state_tracker import StateTracker


class SQLiteSessionStore:
    """Persists complete session snapshots in SQLite with metrics and logging."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        if self.db_path.parent != Path("."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            # Основная таблица сессий
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    player_name TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Таблица для логирования диалогов (для аналитики)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dialog_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    npc_id TEXT NOT NULL,
                    player_message TEXT NOT NULL,
                    npc_response TEXT NOT NULL,
                    intent TEXT,
                    nlg_source TEXT,
                    response_time_ms REAL,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
                """
            )

            # Таблица для метрик (статистика использования)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
                """
            )

            # Таблица для отслеживания ошибок
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    npc_id TEXT,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            conn.commit()

    def save(self, state: StateTracker) -> None:
        payload = state.to_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, player_name, payload, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (state.session_id, state.player.player_name, payload),
            )

    def log_dialog(self, session_id: str, npc_id: str, player_message: str,
                   npc_response: str, intent: str = "", nlg_source: str = "",
                   response_time_ms: float = 0.0) -> None:
        """Залогировать диалоговый оборот для аналитики."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dialog_logs
                (session_id, npc_id, player_message, npc_response, intent, nlg_source, response_time_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, npc_id, player_message, npc_response, intent, nlg_source, response_time_ms)
            )

    def log_metric(self, session_id: str, metric_name: str, metric_value: float) -> None:
        """Залогировать метрику."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metrics (session_id, metric_name, metric_value)
                VALUES (?, ?, ?)
                """,
                (session_id, metric_name, metric_value)
            )

    def log_error(self, error_type: str, error_message: str,
                  session_id: Optional[str] = None, npc_id: Optional[str] = None) -> None:
        """Залогировать ошибку для отладки."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO error_logs (session_id, error_type, error_message, npc_id)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, error_type, error_message, npc_id)
            )

    def get_session_stats(self, session_id: str) -> Dict:
        """Получить статистику по сессии."""
        with self._connect() as conn:
            # Количество диалогов
            dialog_count = conn.execute(
                "SELECT COUNT(*) FROM dialog_logs WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0]

            # Распределение NLG источников
            sources = conn.execute(
                """
                SELECT nlg_source, COUNT(*) as count
                FROM dialog_logs WHERE session_id = ?
                GROUP BY nlg_source
                """,
                (session_id,)
            ).fetchall()

            # Среднее время ответа
            avg_time = conn.execute(
                "SELECT AVG(response_time_ms) FROM dialog_logs WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0.0

            return {
                "total_dialogs": dialog_count,
                "nlg_sources": {source: count for source, count in sources},
                "avg_response_time_ms": round(avg_time, 2),
            }

    def load_all(self) -> Dict[str, StateTracker]:
        sessions: Dict[str, StateTracker] = {}
        with self._connect() as conn:
            rows = conn.execute("SELECT session_id, payload FROM sessions").fetchall()

        for session_id, payload in rows:
            try:
                sessions[session_id] = StateTracker.from_dict(json.loads(payload))
            except (TypeError, json.JSONDecodeError, sqlite3.DatabaseError):
                continue

        return sessions
