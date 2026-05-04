"""
forge.code_intelligence.atlas — Atlas schema management integration.

Atlas is a language-agnostic binary for database schema migrations.
We call it via subprocess. It connects to a database URL and applies
migration files generated from DatabaseSchema output.

Usage:
    am = Atlas.discover()
    if am:
        am.apply(schema_sql="CREATE TABLE users (...)", url="postgres://...")
        am.new_migration(url="postgres://...", out_dir="migrations/")
        status = am.status(url="postgres://...")

Install: curl -fsSL https://atlasproject.io/install.sh | sh
Docs:   https://atlasproject.io/
"""

from __future__ import annotations

import json
import shutil
import structlog
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


@dataclass
class MigrationFile:
    """A single migration file produced by Atlas."""
    name: str          # e.g. "20240101120000_create_users.sql"
    version: str       # e.g. "20240101120000"
    up: str            # SQL statements (applied on migrate up)
    down: Optional[str]  # SQL statements (applied on migrate down), if present
    path: Path


@dataclass
class MigrationStatus:
    """Current state of the migration stack."""
    current: Optional[str]   # current applied version, or None
    pending: list[str]       # versions not yet applied
    applied: list[str]       # already applied versions
    total: int


class Atlas:
    """
    Atlas schema management wrapper.

    Manages database migrations and schema applications using the Atlas binary.
    Requires: Atlas installed (see install command above)

    Two modes:
    - File-based: write migration SQL files to a migrations/ directory
    - Direct apply: apply a schema SQL string directly to a database URL
    """

    def __init__(self, binary: str = "atlas"):
        self._bin = binary

    # ── Discovery ─────────────────────────────────────────────────────────────

    @classmethod
    def discover(cls, binary: str = "atlas") -> Optional["Atlas"]:
        """
        Find the atlas binary and return an Atlas instance.
        Returns None if atlas is not installed.
        """
        path = shutil.which(binary)
        if not path:
            log.warning("atlas.not_found", hint="Install: curl -fsSL https://atlasproject.io/install.sh | sh")
            return None

        try:
            result = subprocess.run(
                [path, "version"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                version_line = result.stdout.splitlines()[0] if result.stdout else "unknown"
                log.info("atlas.found", binary=path, version=version_line)
                return cls(binary)
        except Exception as e:
            log.warning("atlas.version_check_failed", error=str(e))

        return None

    # ── Core operations ────────────────────────────────────────────────────────

    def apply(
        self,
        url: str,
        schema_sql: str,
        *,
        dev_url: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Apply schema SQL directly to a database URL.

        Atlas computes the diff between the current database state and the
        desired schema, then applies only the necessary statements.

        Parameters
        ----------
        url:
            Database URL (postgres://user:pass@host:5432/db, mysql://..., etc.)
        schema_sql:
            The desired schema as SQL DDL statements.
        dev_url:
            Optional dev/testing database URL for schema inspection.
        dry_run:
            If True, only print the planned SQL without executing.

        Returns
        -------
        dict with keys: planned (list of SQL statements), executed (bool),
                        output (str), error (str or None)
        """
        env = {**__import__("os").environ, "ATLAS_NO_VERSION_CHECK": "1"}

        args = [
            self._bin, "schema", "apply",
            "--url", url,
            "--schema", "sql",   # input is raw SQL, not HCL or other format
            "--dry-run" if dry_run else "--auto-approve",
        ]

        if dev_url:
            args.extend(["--dev-url", dev_url])

        try:
            result = subprocess.run(
                args,
                input=schema_sql,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        except FileNotFoundError:
            return {"error": f"atlas binary not found at {self._bin}", "executed": False, "planned": [], "output": ""}
        except subprocess.TimeoutExpired:
            return {"error": "atlas apply timed out after 120s", "executed": False, "planned": [], "output": ""}

        output = result.stdout + result.stderr
        executed = result.returncode == 0 and not dry_run

        if result.returncode != 0:
            log.error("atlas.apply_failed", stderr=result.stderr[:500])
            return {"error": result.stderr[:500], "executed": False, "planned": [], "output": output}

        # Parse planned SQL from output
        planned = self._extract_planned_sql(output)

        return {"executed": executed, "planned": planned, "output": output, "error": None}

    def new_migration(
        self,
        url: str,
        *,
        dir: str | Path = "migrations",
        format: str = "sql",
    ) -> list[MigrationFile]:
        """
        Generate a new migration file in the migrations directory.

        Atlas compares the current database state with the desired state
        (from the migration directory's current state) and writes a new
        migration file with the delta.

        Parameters
        ----------
        url:
            Database URL (determines current state by inspecting the database).
        dir:
            Directory to write migration files (created if it doesn't exist).
            Should contain existing migration files managed by Atlas.
        format:
            "sql" (default) or "golang-migrate".

        Returns
        -------
        List of MigrationFile objects (usually one, the newly created one).
        """
        dir_path = Path(dir)
        env = {**__import__("os").environ, "ATLAS_NO_VERSION_CHECK": "1"}

        args = [
            self._bin, "migrate", "new",
            "--dir", f"file://{dir_path}",
            "--editor", "cat",   # Don't open an editor, just print the path
            "--format", "{{ .Name }}:{{ .Version }}",
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
        except FileNotFoundError:
            log.error("atlas.binary_missing")
            return []
        except subprocess.TimeoutExpired:
            log.error("atlas.new_migration.timeout")
            return []

        if result.returncode != 0:
            log.error("atlas.new_migration_failed", stderr=result.stderr[:300])
            return []

        # Atlas prints the path of the new migration file
        new_files = []
        for line in result.stdout.splitlines():
            if ":" in line:
                name, version = line.strip().split(":", 1)
                sql_path = dir_path / name
                if sql_path.exists():
                    sql_content = sql_path.read_text()
                    up, down = self._split_migration_sql(sql_content)
                    new_files.append(MigrationFile(
                        name=name,
                        version=version,
                        up=up,
                        down=down,
                        path=sql_path,
                    ))

        log.info("atlas.new_migration", count=len(new_files), files=[str(f.path) for f in new_files])
        return new_files

    def status(self, url: str, *, dir: str | Path = "migrations") -> MigrationStatus:
        """
        Return the current migration status: which version is applied and
        which migrations are pending.

        Parameters
        ----------
        url:
            Database URL to inspect.
        dir:
            Migration directory (contains migration files managed by Atlas).

        Returns
        -------
        MigrationStatus object.
        """
        env = {**__import__("os").environ, "ATLAS_NO_VERSION_CHECK": "1"}

        args = [
            self._bin, "migrate", "status",
            "--url", url,
            "--dir", f"file://{Path(dir)}",
            "--format", "{{ json . }}",
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
        except FileNotFoundError:
            return MigrationStatus(current=None, pending=[], applied=[], total=0)
        except subprocess.TimeoutExpired:
            return MigrationStatus(current=None, pending=[], applied=[], total=0)

        if result.returncode != 0:
            log.warning("atlas.status_failed", stderr=result.stderr[:300])
            return MigrationStatus(current=None, pending=[], applied=[], total=0)

        return self._parse_status_json(result.stdout)

    def lint(self, migration_dir: str | Path) -> list[str]:
        """
        Lint migration files in a directory using Atlas's built-in analysis.

        Returns a list of lint error messages (empty if no issues).
        """
        env = {**__import__("os").environ, "ATLAS_NO_VERSION_CHECK": "1"}

        args = [
            self._bin, "migrate", "lint",
            "--dir", f"file://{Path(migration_dir)}",
            "--format", "{{ .Error }}",
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
        except FileNotFoundError:
            return []
        except subprocess.TimeoutExpired:
            return []

        errors = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return errors

    # ── Helpers ─────────────────────────────────────────────────────────────────

    def _extract_planned_sql(self, output: str) -> list[str]:
        """Extract planned SQL statements from atlas apply output."""
        statements = []
        in_sql = False
        for line in output.splitlines():
            if "--" in line or "CREATE" in line.upper() or "ALTER" in line.upper() or "DROP" in line.upper():
                in_sql = True
            if in_sql:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and stripped != "--":
                    statements.append(stripped)
                if stripped.endswith(";"):
                    in_sql = False
        return statements

    def _split_migration_sql(self, content: str) -> tuple[str, Optional[str]]:
        """Split a migration file into up and down blocks."""
        # Atlas SQL migrations use -- +up and -- +down comments
        parts = content.split("-- +migrate Up")
        if len(parts) < 2:
            return content.strip(), None

        up_and_down = parts[1].split("-- +migrate Down")
        up = up_and_down[0].strip().replace("-- +migrate Down", "").strip()
        down = up_and_down[1].strip() if len(up_and_down) > 1 else None
        return up, down

    def _parse_status_json(self, stdout: str) -> MigrationStatus:
        """Parse Atlas's JSON status output."""
        try:
            data = json.loads(stdout.strip())
            current = data.get("current", "")
            pending = [p.get("version", "") for p in data.get("pending", [])]
            applied = [a.get("version", "") for a in data.get("applied", [])]
            return MigrationStatus(
                current=current or None,
                pending=pending,
                applied=applied,
                total=len(pending) + len(applied),
            )
        except Exception:
            return MigrationStatus(current=None, pending=[], applied=[], total=0)
