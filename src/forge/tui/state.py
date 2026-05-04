"""
forge.tui.state — Global application state for the Forge TUI.

Replaces the dataclass AppContext with a full singleton that owns:
  - SQLite session store (chat sessions with token counts & costs)
  - Current LLM backend (loaded from forge config)
  - Pub/sub event bus (publish/subscribe for all TUI components)
  - Permission state (pending requests, session-level grants)
  - Status bar queue (TTL-based auto-dismissing messages)

Components subscribe to event channels they care about:
  - "session"     → session created/updated/deleted
  - "status"      → status bar messages (info/warn/error with TTL)
  - "permission"  → permission requests (blocking dialogs)
  - "model"       → model changed
  - "stream"      → LLM token stream events
  - "tool_call"   → tool call start/complete/error
"""

from __future__ import annotations

import json
import uuid
import threading
import time
import fnmatch
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Any
from enum import Enum

import sqlite3

from forge.permissions import PermissionResult, PermissionRequired


# ── Enums ──────────────────────────────────────────────────────────────────────

class StatusLevel(Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SUCCESS = "success"


# ── Event payload types ────────────────────────────────────────────────────────

@dataclass
class StatusMsg:
    level: StatusLevel
    text: str
    ttl: float = 5.0  # seconds, 0 = persistent
    created_at: float = field(default_factory=time.time)


@dataclass
class StreamEvent:
    session_id: str
    kind: str  # "token" | "thinking" | "tool_start" | "tool_result" | "done" | "error"
    content: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    tool_result: str = ""
    is_error: bool = False


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    id: str
    project_name: str
    title: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    messages: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(cls, project_name: str, model: str, provider: str) -> "Session":
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=str(uuid.uuid4()),
            project_name=project_name,
            title="New Chat",
            model=model,
            provider=provider,
            created_at=now,
            updated_at=now,
        )


# ── EventBus ───────────────────────────────────────────────────────────────────

class EventBus:
    """
    Thread-safe publish/subscribe event bus.

    Channels:
        "session"     → Session events
        "status"      → StatusMsg
        "permission"  → PermissionRequest
        "model"       → (provider, model)
        "stream"      → StreamEvent
        "tool_call"   → dict (tool call metadata)
    """

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, channel: str, callback: Callable) -> None:
        with self._lock:
            self._subscribers.setdefault(channel, []).append(callback)

    def unsubscribe(self, channel: str, callback: Callable) -> None:
        with self._lock:
            if channel in self._subscribers:
                self._subscribers[channel] = [
                    cb for cb in self._subscribers[channel] if cb != callback
                ]

    def publish(self, channel: str, payload: Any) -> None:
        with self._lock:
            callbacks = list(self._subscribers.get(channel, []))

        for cb in callbacks:
            try:
                cb(payload)
            except Exception:
                pass  # Never let one subscriber crash the bus


# ── PermissionGate ─────────────────────────────────────────────────────────────

@dataclass
class PermissionRequest:
    tool: str
    action: str
    path: str
    description: str
    agent: str = "build"

    @property
    def id(self) -> str:
        return f"{self.tool}:{self.action}:{self.path}"


# ── AppState ───────────────────────────────────────────────────────────────────

class AppState:
    """
    Global singleton — owns all shared state for the Forge TUI.

    Lives on the main thread. All writes go through here.
    Background threads (LLM worker, permission checker) publish events
    via the event bus, never mutate state directly.
    """

    _instance: Optional["AppState"] = None

    def __init__(self):
        # Event bus
        self.bus = EventBus()

        # Configured backend
        self._backend = None  # set by _load_backend()

        # Current model (provider/model strings)
        self.current_provider: str = ""
        self.current_model: str = ""

        # Session store (SQLite at ~/.forge/sessions.db)
        self._sessions_db: Optional[Path] = None
        self._sessions_conn: Optional[sqlite3.Connection] = None
        self._current_session: Optional[Session] = None

        # Permission grants (session-level, cleared on new session)
        self._granted: dict[str, bool] = {}  # key → True/False

        # Status bar queue
        self._status_queue: list[StatusMsg] = []
        self._status_lock = threading.Lock()

        # Load config
        self._load_backend()

    @classmethod
    def get(cls) -> "AppState":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Backend / Model ───────────────────────────────────────────────────────

    def _load_backend(self) -> None:
        """Load LLM backend from forge config."""
        try:
            from forge.llm import load_config, create_backend
            cfg = load_config()
            if cfg and cfg.provider:
                self._backend = create_backend(cfg)
                self.current_provider = cfg.provider
                self.current_model = cfg.model or ""
        except Exception:
            self._backend = None

    def reload_backend(self) -> None:
        """Reload backend after config change."""
        self._load_backend()
        self.bus.publish("model", (self.current_provider, self.current_model))

    def get_backend(self):
        """Return the current LLM backend."""
        return self._backend

    def update_model(self, provider: str, model: str) -> None:
        """Switch to a different provider/model."""
        from forge.llm import LLMConfig, create_backend
        try:
            cfg = LLMConfig(provider=provider, model=model)
            self._backend = create_backend(cfg)
            self.current_provider = provider
            self.current_model = model
            self.bus.publish("model", (provider, model))
        except Exception as e:
            self.set_status(f"Failed to load {provider}/{model}: {e}", StatusLevel.ERROR)

    # ── Sessions (SQLite) ─────────────────────────────────────────────────────

    @property
    def sessions_db_path(self) -> Path:
        if self._sessions_db is None:
            p = Path.home() / ".forge" / "sessions.db"
            p.parent.mkdir(parents=True, exist_ok=True)
            self._sessions_db = p
        return self._sessions_db

    def _get_sessions_conn(self) -> sqlite3.Connection:
        if self._sessions_conn is None:
            conn = sqlite3.connect(str(self.sessions_db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    title        TEXT NOT NULL DEFAULT 'New Chat',
                    model        TEXT NOT NULL,
                    provider     TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cost         REAL NOT NULL DEFAULT 0.0,
                    messages     INTEGER NOT NULL DEFAULT 0,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id          TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL REFERENCES sessions(id),
                    role        TEXT NOT NULL,  -- user | assistant | tool
                    content     TEXT NOT NULL,
                    model       TEXT,
                    tool_calls  TEXT,  -- JSON
                    tool_result TEXT,
                    finish_reason TEXT,
                    created_at  TEXT NOT NULL
                )
            """)
            conn.commit()
            self._sessions_conn = conn
        return self._sessions_conn

    def list_sessions(self) -> list[Session]:
        conn = self._get_sessions_conn()
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_session(dict(r)) for r in rows]

    def get_session(self, session_id: str) -> Optional[Session]:
        conn = self._get_sessions_conn()
        row = conn.execute(
            "SELECT * FROM sessions WHERE id=?", [session_id]
        ).fetchone()
        return self._row_to_session(dict(row)) if row else None

    def get_current_session(self) -> Optional[Session]:
        return self._current_session

    def create_session(self, project_name: str, model: str, provider: str) -> Session:
        session = Session.new(project_name, model, provider)
        conn = self._get_sessions_conn()
        conn.execute("""
            INSERT INTO sessions
            (id, project_name, title, model, provider, prompt_tokens, completion_tokens,
             total_tokens, cost, messages, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0.0, 0, ?, ?)
        """, [
            session.id, session.project_name, session.title,
            session.model, session.provider,
            session.created_at, session.updated_at,
        ])
        conn.commit()
        self._current_session = session
        self.bus.publish("session", ("created", session))
        return session

    def set_current_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session:
            self._current_session = session
            self.bus.publish("session", ("selected", session))

    def update_session_tokens(
        self, session_id: str,
        prompt_tokens: int, completion_tokens: int,
        cost: float,
    ) -> None:
        conn = self._get_sessions_conn()
        total = prompt_tokens + completion_tokens
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE sessions
            SET prompt_tokens=?, completion_tokens=?, total_tokens=?,
                cost=?, updated_at=?
            WHERE id=?
        """, [prompt_tokens, completion_tokens, total, cost, now, session_id])
        conn.commit()
        if self._current_session and self._current_session.id == session_id:
            self._current_session.prompt_tokens = prompt_tokens
            self._current_session.completion_tokens = completion_tokens
            self._current_session.total_tokens = total
            self._current_session.cost = cost
        self.bus.publish("session", ("updated", self.get_session(session_id)))

    def increment_session_messages(self, session_id: str) -> None:
        conn = self._get_sessions_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE sessions SET messages=messages+1, updated_at=? WHERE id=?",
            [now, session_id]
        )
        conn.commit()
        if self._current_session and self._current_session.id == session_id:
            self._current_session.messages += 1
        self.bus.publish("session", ("updated", self.get_session(session_id)))

    def update_session_title(self, session_id: str, title: str) -> None:
        conn = self._get_sessions_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            [title, now, session_id]
        )
        conn.commit()
        if self._current_session and self._current_session.id == session_id:
            self._current_session.title = title
        self.bus.publish("session", ("updated", self.get_session(session_id)))

    def save_message(
        self, session_id: str, role: str, content: str,
        model: Optional[str] = None,
        tool_calls: Optional[list] = None,
        tool_result: Optional[str] = None,
        finish_reason: Optional[str] = None,
    ) -> str:
        conn = self._get_sessions_conn()
        msg_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO messages
            (id, session_id, role, content, model, tool_calls, tool_result, finish_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            msg_id, session_id, role, content, model,
            json.dumps(tool_calls) if tool_calls else None,
            tool_result, finish_reason, now,
        ])
        conn.commit()
        return msg_id

    def get_session_messages(self, session_id: str) -> list[dict]:
        conn = self._get_sessions_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at",
            [session_id]
        ).fetchall()
        return [dict(r) for r in rows]

    def _row_to_session(self, row: dict) -> Session:
        return Session(
            id=row["id"],
            project_name=row["project_name"],
            title=row["title"],
            model=row["model"],
            provider=row["provider"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            cost=row["cost"],
            messages=row["messages"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Status bar ────────────────────────────────────────────────────────────

    def set_status(
        self, text: str,
        level: StatusLevel = StatusLevel.INFO,
        ttl: float = 5.0,
    ) -> None:
        """Publish a status message. ttl=0 means persistent (user must dismiss)."""
        msg = StatusMsg(level=level, text=text, ttl=ttl)
        with self._status_lock:
            # Replace any existing message of same level (avoid spam)
            self._status_queue = [m for m in self._status_queue if m.level != level]
            self._status_queue.append(msg)
        self.bus.publish("status", msg)

    def pop_status(self) -> Optional[StatusMsg]:
        with self._status_lock:
            if not self._status_queue:
                return None
            return self._status_queue.pop(0)

    def peek_status(self) -> Optional[StatusMsg]:
        with self._status_lock:
            return self._status_queue[0] if self._status_queue else None

    # ── Permissions ──────────────────────────────────────────────────────────

    def check_permission(self, tool: str, action: str, path: str, agent: str = "build") -> bool:
        """
        Check forge.permissions rules. Returns True/False.
        If the rule is ASK, publishes a permission request to the bus
        and blocks waiting for user response.
        """
        from forge.permissions import check_permission as _check

        result = _check(agent, action, path)
        key = f"{tool}:{action}:{path}"

        # Already granted this session?
        if self._granted.get(key):
            return True

        if result == PermissionResult.ALLOW:
            return True
        elif result == PermissionResult.DENY:
            return False
        else:  # ASK
            req = PermissionRequest(
                tool=tool, action=action, path=path,
                description=f"{action} {path} via {tool}",
                agent=agent,
            )
            # Publish and wait for dialog to call grant/deny
            self.bus.publish("permission", req)
            # The permission dialog will call grant_permission() or deny_permission()
            return False  # Caller should await resolution

    def grant_permission(self, key: str, persistent: bool = False) -> None:
        self._granted[key] = True
        self.bus.publish("permission_resolved", (key, True, persistent))

    def deny_permission(self, key: str) -> None:
        self._granted[key] = False
        self.bus.publish("permission_resolved", (key, False, False))

    def clear_session_permissions(self) -> None:
        """Clear all session-level grants (called on new session)."""
        self._granted = {}

    # ── Projects (discovers from ~/.forge/projects/) ─────────────────────────

    def list_projects(self) -> list[tuple[str, Path, float]]:
        """
        List all Forge projects by scanning ~/.forge/projects/.
        Returns [(project_name, path, mtime)] sorted newest first.
        """
        root = Path.home() / ".forge" / "projects"
        if not root.exists():
            return []
        candidates = [
            (p.name, p, p.stat().st_mtime)
            for p in root.iterdir()
            if p.is_dir()
        ]
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates
