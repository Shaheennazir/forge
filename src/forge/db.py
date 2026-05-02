"""
forge SQLite schema — 4-tier memory, spec versioning, failure lanes.

Tiers:
  short   — in-memory only, current task
  mid     — current project session (days)
  episodic — across sessions, per project (weeks)
  long    — cross-project (months)

Failure propagation: task_run records carry status (pending|running|done|blocked|failed).
Blocked tasks expose failure_reason for orchestrator decision.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

SCHEMA = """
-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,  -- ISO UTC
    updated_at  TEXT NOT NULL
);

-- Spec versions (append-only changelog discipline)
CREATE TABLE IF NOT EXISTS spec_versions (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id),
    version      INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    prompt       TEXT NOT NULL,         -- original user prompt
    spec_md      TEXT NOT NULL,         -- full SPEC.md content
    changelog    TEXT NOT NULL DEFAULT '',  -- human-readable delta
    UNIQUE(project_id, version)
);

-- Tasks (graph nodes: spec_gen, exec, review, subagent_delegate…)
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    spec_version    INTEGER NOT NULL REFERENCES spec_versions(version),
    label           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
                        -- pending | running | done | blocked | failed
    failure_reason  TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT
);

-- Task edges (directed graph: from_id → to_id)
CREATE TABLE IF NOT EXISTS task_edges (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    from_task   TEXT NOT NULL REFERENCES tasks(id),
    to_task     TEXT NOT NULL REFERENCES tasks(id),
    edge_type   TEXT NOT NULL DEFAULT 'normal',
                -- normal | review_pass | review_fail | retry | escalate
    UNIQUE(from_task, to_task)
);

-- Memory entries (short/mid/episodic/long)
CREATE TABLE IF NOT EXISTS memory (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    tier        TEXT NOT NULL CHECK(tier IN ('short','mid','episodic','long')),
    agent       TEXT NOT NULL,       -- 'orchestrator' | 'spec_gen' | 'executor' | 'review_gate' | <subagent>
    key         TEXT NOT NULL,       -- dotpath key: 'spec.features.0.name'
    value       TEXT NOT NULL,       -- JSON serialized
    created_at  TEXT NOT NULL,
    expires_at  TEXT,                 -- NULL = no expiry
    UNIQUE(project_id, tier, agent, key)
);

-- Subagent runs (failure tracking)
CREATE TABLE IF NOT EXISTS subagent_runs (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    agent_type      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
                    -- running | done | failed | escalated
    failure_reason  TEXT,
    output_summary  TEXT,
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tasks_project   ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_memory_project_tier ON memory(project_id, tier);
CREATE INDEX IF NOT EXISTS idx_memory_expires  ON memory(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_subagent_task   ON subagent_runs(task_id);
"""

# Pre-split schema statements — avoids regex fragility in statement parsing
_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS spec_versions (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(id),
    version      INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    spec_md      TEXT NOT NULL,
    changelog    TEXT NOT NULL DEFAULT '',
    UNIQUE(project_id, version)
)""",
    """CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    spec_version    INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    failure_reason  TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    label           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT
)""",
    """CREATE TABLE IF NOT EXISTS task_edges (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    from_task   TEXT NOT NULL REFERENCES tasks(id),
    to_task     TEXT NOT NULL REFERENCES tasks(id),
    edge_type   TEXT NOT NULL DEFAULT 'control',
    created_at  TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS memory (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    tier        TEXT NOT NULL,
    agent       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    UNIQUE(project_id, tier, agent, key)
)""",
    """CREATE TABLE IF NOT EXISTS subagent_runs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    failure_reason  TEXT,
    parent_run_id   TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    result          TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_tasks_project    ON tasks(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_spec_ver   ON tasks(spec_version)",
    "CREATE INDEX IF NOT EXISTS idx_edges_project    ON task_edges(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_from       ON task_edges(from_task)",
    "CREATE INDEX IF NOT EXISTS idx_memory_project_tier ON memory(project_id, tier)",
    "CREATE INDEX IF NOT EXISTS idx_memory_expires  ON memory(expires_at) WHERE expires_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_subagent_task   ON subagent_runs(task_id)",
]


@dataclass
class Project:
    id: str
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass
class SpecVersion:
    id: str
    project_id: str
    version: int
    created_at: datetime
    prompt: str
    spec_md: str
    changelog: str = ""


@dataclass
class Task:
    id: str
    project_id: str
    spec_version: int
    label: str
    status: str  # pending | running | done | blocked | failed
    failure_reason: Optional[str] = None
    retry_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


@dataclass
class MemoryEntry:
    id: str
    project_id: str
    tier: str  # short | mid | episodic | long
    agent: str
    key: str
    value: str  # JSON
    created_at: datetime
    expires_at: Optional[datetime] = None


class ForgeDB:
    """Per-project SQLite database with 4-tier memory and failure tracking."""

    def __init__(self, project_id: str, db_path: Optional[Path] = None):
        self.project_id = project_id
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = Path.home() / ".forge" / "projects" / project_id / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        return self._conn

    def _init_schema(self):
        """Initialize schema statements one at a time."""
        conn = self._conn
        conn.execute("PRAGMA foreign_keys = ON")
        for stmt in _SCHEMA_STMTS:
            conn.execute(stmt)
        # No explicit BEGIN needed; each execute auto-commits via Python's sqlite3 default

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Projects ─────────────────────────────────────────────────────────────

    def upsert_project(self, name: str) -> Project:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO projects (id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at
        """, [self.project_id, name, now, now])
        conn.commit()
        row = conn.execute(
            "SELECT * FROM projects WHERE id=?", [self.project_id]
        ).fetchone()
        return dict_to_project(dict(row))

    # ── Spec versioning ─────────────────────────────────────────────────────────

    def latest_spec_version(self) -> Optional[int]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT MAX(version) as v FROM spec_versions WHERE project_id=?
        """, [self.project_id]).fetchone()
        return row["v"] if row and row["v"] is not None else None

    def save_spec_version(
        self, version: int, prompt: str, spec_md: str, changelog: str = ""
    ) -> SpecVersion:
        now = datetime.now(timezone.utc).isoformat()
        spec_id = f"spec_{self.project_id}_{version}"
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO spec_versions (id, project_id, version, created_at, prompt, spec_md, changelog)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [spec_id, self.project_id, version, now, prompt, spec_md, changelog])
        conn.commit()
        row = conn.execute(
            "SELECT * FROM spec_versions WHERE id=?", [spec_id]
        ).fetchone()
        return dict_to_spec_version(dict(row))

    def get_spec_version(self, version: int) -> Optional[SpecVersion]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM spec_versions WHERE project_id=? AND version=?
        """, [self.project_id, version]).fetchone()
        return dict_to_spec_version(dict(row)) if row else None

    # ── Tasks ──────────────────────────────────────────────────────────────────

    def create_task(
        self, task_id: str, spec_version: int, label: str
    ) -> Task:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO tasks (id, project_id, spec_version, label, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, [task_id, self.project_id, spec_version, label, now, now])
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", [task_id]).fetchone()
        return dict_to_task(dict(row))

    def set_task_status(
        self, task_id: str, status: str, failure_reason: Optional[str] = None
    ):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        completed = now if status in ("done", "failed", "blocked") else None
        conn.execute("""
            UPDATE tasks
            SET status=?, failure_reason=?, updated_at=?, completed_at=?
            WHERE id=?
        """, [status, failure_reason, now, completed, task_id])
        conn.commit()

    def get_task(self, task_id: str) -> Optional[Task]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", [task_id]).fetchone()
        return dict_to_task(dict(row)) if row else None

    def get_tasks_by_status(self, status: str) -> list[Task]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id=? AND status=?", [self.project_id, status]
        ).fetchall()
        return [dict_to_task(dict(r)) for r in rows]

    def add_task_edge(self, edge_id: str, from_task: str, to_task: str, edge_type: str = "normal"):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT OR IGNORE INTO task_edges (id, project_id, from_task, to_task, edge_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [edge_id, self.project_id, from_task, to_task, edge_type, now])
        conn.commit()

    def get_task_graph(self) -> tuple[list[Task], list[dict]]:
        """Return all tasks and edges for this project."""
        conn = self._get_conn()
        tasks = [
            dict_to_task(dict(r))
            for r in conn.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at", [self.project_id]
            ).fetchall()
        ]
        edges = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM task_edges WHERE project_id=?", [self.project_id]
            ).fetchall()
        ]
        return tasks, edges

    # ── Memory ─────────────────────────────────────────────────────────────────

    def write_memory(
        self,
        tier: str,
        agent: str,
        key: str,
        value: str,
        expires_at: Optional[datetime] = None,
    ):
        import uuid
        conn = self._get_conn()
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        exp = expires_at.isoformat() if expires_at else None
        # Upsert via DELETE + INSERT (avoids ON CONFLICT column-count issues)
        conn.execute(
            "DELETE FROM memory WHERE project_id=? AND tier=? AND agent=? AND key=?",
            [self.project_id, tier, agent, key]
        )
        conn.execute(
            """INSERT INTO memory (id, project_id, tier, agent, key, value, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [mem_id, self.project_id, tier, agent, key, value, now, exp]
        )
        conn.commit()

    def read_memory(self, tier: str, agent: str, key: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT value FROM memory
            WHERE project_id=? AND tier=? AND agent=? AND key=? AND (expires_at IS NULL OR expires_at > ?)
        """, [self.project_id, tier, agent, key, datetime.now(timezone.utc).isoformat()]).fetchone()
        return row["value"] if row else None

    def query_memory(self, tier: Optional[str] = None, agent: Optional[str] = None, key_prefix: Optional[str] = None) -> list[MemoryEntry]:
        conn = self._get_conn()
        q = ["SELECT * FROM memory WHERE project_id=? AND (expires_at IS NULL OR expires_at > ?)"]
        args: list = [self.project_id, datetime.now(timezone.utc).isoformat()]
        if tier:
            q.append("AND tier=?")
            args.append(tier)
        if agent:
            q.append("AND agent=?")
            args.append(agent)
        if key_prefix:
            q.append("AND key LIKE ?")
            args.append(f"{key_prefix}%")
        rows = conn.execute(" ".join(q), args).fetchall()
        return [dict_to_memory(dict(r)) for r in rows]

    def purge_expired(self):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM memory WHERE expires_at IS NOT NULL AND expires_at <= ?",
            [datetime.now(timezone.utc).isoformat()]
        )
        conn.commit()

    # ── Subagent failure tracking ─────────────────────────────────────────────

    def create_subagent_run(self, run_id: str, task_id: str, agent_type: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO subagent_runs (id, project_id, task_id, name, status, created_at)
            VALUES (?, ?, ?, ?, 'running', ?)
        """, [run_id, self.project_id, task_id, agent_type, now])
        conn.commit()
        return run_id

    def finish_subagent_run(self, run_id: str, status: str, failure_reason: Optional[str] = None, output_summary: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE subagent_runs
            SET status=?, failure_reason=?, result=?, completed_at=?
            WHERE id=?
        """, [status, failure_reason, output_summary, now, run_id])
        conn.commit()

    def get_blocked_tasks(self) -> list[Task]:
        return self.get_tasks_by_status("blocked")

    def get_incomplete_subagent_runs(self) -> list[dict]:
        """Return all subagent runs that are still 'running' (orphaned from interrupted sessions)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM subagent_runs WHERE status='running' AND project_id=?",
            [self.project_id],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_failed_subagent_runs(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM subagent_runs WHERE status='failed' OR status='escalated'
        """).fetchall()
        return [dict(r) for r in rows]


# ── Row -> dataclass helpers ───────────────────────────────────────────────────

def dict_to_project(d: dict) -> Project:
    return Project(
        id=d["id"], name=d["name"],
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
    )

def dict_to_spec_version(d: dict) -> SpecVersion:
    return SpecVersion(
        id=d["id"], project_id=d["project_id"], version=d["version"],
        created_at=datetime.fromisoformat(d["created_at"]),
        prompt=d["prompt"], spec_md=d["spec_md"], changelog=d.get("changelog", ""),
    )

def dict_to_task(d: dict) -> Task:
    return Task(
        id=d["id"], project_id=d["project_id"], spec_version=d["spec_version"],
        label=d["label"], status=d["status"],
        failure_reason=d.get("failure_reason"),
        retry_count=d.get("retry_count", 0),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
    )

def dict_to_memory(d: dict) -> MemoryEntry:
    return MemoryEntry(
        id=d["id"], project_id=d["project_id"], tier=d["tier"], agent=d["agent"],
        key=d["key"], value=d["value"],
        created_at=datetime.fromisoformat(d["created_at"]),
        expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
    )
