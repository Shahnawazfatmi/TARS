"""
FATMEE's memory, in one local SQLite file (backend/data/fatmee.db).

- Working memory (ConversationStore): what was said, by session, so
  follow-up questions make sense.
- Episodic memory (EpisodeStore): what FATMEE did. One diary entry
  per task: when, what, which tools, how it went.

Semantic memory (people, facts) will be added to this same file later.
"""

import json
import logging
import os
import sqlite3
import threading
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import config


logger = logging.getLogger("fatmee.memory")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT    NOT NULL,
    task_id     TEXT,
    meta        TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages (session_id, id);
"""


class _SQLiteStore:
    """Shared connection handling for every FATMEE memory table."""

    schema = ""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.DB_PATH

        folder = os.path.dirname(self.db_path)

        if folder:
            os.makedirs(folder, exist_ok=True)

        # One writer at a time; reads are cheap with a new connection.
        self._lock = threading.Lock()

        with self._connect() as conn:
            # WAL lets reads continue while a write happens; the
            # setting is stored in the file, so this runs once.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(self.schema)

    @contextmanager
    def _connect(self):
        """A connection that commits on success and always closes."""

        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row

        try:
            yield conn
            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()


class ConversationStore(_SQLiteStore):
    """Read and write conversation messages in SQLite."""

    schema = _SCHEMA

    def add_exchange(
        self,
        session_id: str,
        question: str,
        answer: str,
        task_id: str | None = None,
        meta: dict | None = None,
    ) -> None:
        """Save one question and its answer together."""

        now = datetime.now().isoformat(timespec="seconds")
        meta_json = json.dumps(meta) if meta else None

        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO messages (session_id, role, content, task_id, meta, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (session_id, "user", question, task_id, None, now),
                    (session_id, "assistant", answer, task_id, meta_json, now),
                ],
            )

    def recent(self, session_id: str, limit: int | None = None) -> list:
        """The last `limit` messages, oldest first."""

        limit = config.HISTORY_MESSAGES if limit is None else limit

        if limit <= 0:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()

        return [dict(row) for row in reversed(rows)]

    def messages(self, session_id: str) -> list:
        """Every message in a session, oldest first."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, task_id, meta, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

        result = []

        for row in rows:
            item = dict(row)
            item["meta"] = json.loads(item["meta"]) if item["meta"] else None
            result.append(item)

        return result

    def clear(self, session_id: str) -> int:
        """Delete a session's messages. Returns how many were deleted."""

        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )

        return cursor.rowcount


def format_history(history: list, max_chars: int = config.HISTORY_CHARS) -> str:
    """
    Turn recent messages into a prompt section.

    Returns "" when there is no history, so prompts stay unchanged
    for the first question of a conversation.
    """

    if not history:
        return ""

    lines = []

    for message in history:
        who = "User" if message["role"] == "user" else "FATMEE"
        text = message["content"].strip()

        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + " ...(shortened)"

        lines.append(f"{who}: {text}")

    return (
        "CONVERSATION SO FAR (oldest first; use it to understand "
        "follow-up questions like 'and the free space?'):\n"
        + "\n".join(lines)
        + "\n"
    )


# ============================================================
# EPISODIC MEMORY
# ============================================================

EPISODE_STATUSES = ("answered", "partial", "failed", "waiting_approval")

_EPISODE_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    task_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    duration_s  REAL,
    question    TEXT NOT NULL,
    goal        TEXT,
    tools       TEXT,
    status      TEXT NOT NULL,
    summary     TEXT,
    problems    TEXT
);

CREATE INDEX IF NOT EXISTS idx_episodes_started
    ON episodes (started_at);

CREATE INDEX IF NOT EXISTS idx_episodes_status
    ON episodes (status, started_at);

-- Full-text index for keyword search ("did I check my battery?")
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    task_id UNINDEXED, question, goal, summary
);
"""

# Characters kept from each answer in the diary (the full answer is
# already in the conversation table).
SUMMARY_CHARS = 300

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def resolve_period(day: str = "", days_back: int = 0, now: datetime | None = None) -> tuple:
    """
    Turn "today", "yesterday", "monday" or "2026-10-12" into a time range.

    Dates are worked out here from the PC's clock, not by the AI, so
    "yesterday" is always correct. Returns (start, end, label); start
    and end are None when no period was asked for.
    """

    now = now or datetime.now()
    today = now.date()
    word = (day or "").strip().lower()

    if word:

        if word == "today":
            target, label = today, "today"

        elif word == "yesterday":
            target, label = today - timedelta(days=1), "yesterday"

        elif word in _WEEKDAYS:
            # The most recent such day; "monday" on a Monday means today.
            back = (today.weekday() - _WEEKDAYS.index(word)) % 7
            target, label = today - timedelta(days=back), word.capitalize()

        else:
            try:
                target = date.fromisoformat(word)
            except ValueError:
                raise ValueError(
                    f"Unknown day {day!r}. Use today, yesterday, a weekday "
                    "name, or a date like 2026-10-12."
                )
            label = word

        start = datetime.combine(target, datetime.min.time())
        return start, start + timedelta(days=1), f"{label} ({target.isoformat()})"

    if days_back and int(days_back) > 0:
        start = datetime.combine(today - timedelta(days=int(days_back)), datetime.min.time())
        return start, now + timedelta(seconds=1), f"last {int(days_back)} days"

    return None, None, "most recent"


def _fts_query(keyword: str) -> str:
    """Safe full-text query: each word must appear (prefix match)."""

    words = re.findall(r"\w+", keyword.lower())

    return " ".join(f'"{w}"*' for w in words)


class EpisodeStore(_SQLiteStore):
    """FATMEE's diary: one entry per task."""

    schema = _EPISODE_SCHEMA

    def add(
        self,
        task_id: str,
        session_id: str,
        started_at: datetime,
        question: str,
        status: str,
        duration_s: float | None = None,
        goal: str = "",
        tools: list | None = None,
        summary: str = "",
        problems: list | None = None,
    ) -> None:
        """Save (or update) one task's diary entry."""

        if status not in EPISODE_STATUSES:
            raise ValueError(f"Unknown episode status: {status}")

        summary = (summary or "").strip()

        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS].rstrip() + "..."

        row = (
            task_id,
            session_id,
            started_at.isoformat(timespec="seconds"),
            round(duration_s, 2) if duration_s is not None else None,
            question,
            goal,
            json.dumps(tools or []),
            status,
            summary,
            json.dumps(problems or []),
        )

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO episodes (task_id, session_id, started_at, "
                "duration_s, question, goal, tools, status, summary, problems) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            conn.execute("DELETE FROM episodes_fts WHERE task_id = ?", (task_id,))
            conn.execute(
                "INSERT INTO episodes_fts (task_id, question, goal, summary) VALUES (?, ?, ?, ?)",
                (task_id, question, goal, summary),
            )

    def exists(self, task_id: str) -> bool:
        """Is there already a diary entry for this task id?"""

        with self._lock, self._connect() as conn:
            return conn.execute("SELECT 1 FROM episodes WHERE task_id = ?", (task_id,)).fetchone() is not None

    def search(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        keyword: str = "",
        status: str = "",
        session_id: str = "",
        limit: int = 20,
    ) -> tuple:
        """
        Find diary entries, newest first.

        Returns (entries, total_matching). Uses the date and status
        indexes and the full-text index, so it stays fast as the
        diary grows.
        """

        where, params = [], []

        if start is not None:
            where.append("e.started_at >= ?")
            params.append(start.isoformat(timespec="seconds"))

        if end is not None:
            where.append("e.started_at < ?")
            params.append(end.isoformat(timespec="seconds"))

        if status:
            where.append("e.status = ?")
            params.append(status)

        if session_id:
            where.append("e.session_id = ?")
            params.append(session_id)

        join = ""
        query = _fts_query(keyword) if keyword else ""

        if keyword and not query:
            return [], 0

        if query:
            join = "JOIN episodes_fts f ON f.task_id = e.task_id"
            where.append("episodes_fts MATCH ?")
            params.append(query)

        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM episodes e {join} {clause}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT e.* FROM episodes e {join} {clause} "
                "ORDER BY e.started_at DESC LIMIT ?",
                params + [max(1, int(limit))],
            ).fetchall()

        entries = []

        for row in rows:
            item = dict(row)
            item["tools"] = json.loads(item["tools"] or "[]")
            item["problems"] = json.loads(item["problems"] or "[]")
            entries.append(item)

        return entries, total


_episode_store = None
_episode_lock = threading.Lock()


def get_episode_store() -> EpisodeStore:
    """The one shared diary, used by both the server and the recall tool."""

    global _episode_store

    with _episode_lock:
        if _episode_store is None:
            _episode_store = EpisodeStore()

    return _episode_store
