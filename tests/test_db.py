"""Tests for forge.db — SQLite schema and ForgeDB layer."""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from forge.db import ForgeDB


class TestForgeDB:
    @pytest.fixture
    def tmp_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = ForgeDB(project_id="test_proj", db_path=Path(td) / "test.db")
            yield db
            db.close()

    @pytest.fixture
    def project(self, tmp_db):
        """Pre-create project row for FK-dependent tests."""
        tmp_db.upsert_project("Test App")
        return tmp_db

    def test_project_upsert(self, tmp_db):
        p = tmp_db.upsert_project("My App")
        assert p.name == "My App"
        assert p.id == "test_proj"

        # Upsert again — same id
        p2 = tmp_db.upsert_project("My App v2")
        assert p2.name == "My App v2"
        assert p2.id == "test_proj"

    def test_spec_versioning(self, project):
        sv1 = project.save_spec_version(1, "build a web app", "# SPEC\n- web app", "")
        assert sv1.version == 1

        sv2 = project.save_spec_version(2, "add auth", "# SPEC v2\n- auth", "- added auth")
        assert sv2.version == 2

        assert project.latest_spec_version() == 2
        assert project.get_spec_version(1).prompt == "build a web app"
        assert project.get_spec_version(2).changelog == "- added auth"

    def test_task_lifecycle(self, project):
        t = project.create_task("task_1", 1, "Spec Generator")
        assert t.status == "pending"

        project.set_task_status("task_1", "running")
        assert project.get_task("task_1").status == "running"

        project.set_task_status("task_1", "done")
        t = project.get_task("task_1")
        assert t.status == "done"
        assert t.completed_at is not None

    def test_task_edges(self, project):
        project.create_task("n1", 1, "Orchestrator")
        project.create_task("n2", 1, "Spec Generator")
        project.add_task_edge("e1", "n1", "n2", "normal")

        tasks, edges = project.get_task_graph()
        assert len(tasks) == 2
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "normal"

    def test_memory_tiers(self, project):
        # short
        project.write_memory(tier="short", agent="orchestrator", key="greeting", value='"hello"')
        val = project.read_memory(tier="short", agent="orchestrator", key="greeting")
        assert val == '"hello"'

        # overwrite mid
        project.write_memory(tier="mid", agent="orchestrator", key="greeting", value='"hi"')
        val = project.read_memory(tier="mid", agent="orchestrator", key="greeting")
        assert val == '"hi"'

    def test_memory_query(self, project):
        project.write_memory(tier="mid", agent="executor", key="files.generated", value='["a.py"]')
        project.write_memory(tier="mid", agent="executor", key="files.count", value='5')
        project.write_memory(tier="episodic", agent="orchestrator", key="session.last", value='{"node":"executor"}')

        all_mid = project.query_memory(tier="mid", agent="executor")
        assert len(all_mid) == 2

        all_executor = project.query_memory(agent="executor")
        assert len(all_executor) == 2

    def test_blocked_tasks(self, project):
        project.create_task("t1", 1, "Executor")
        project.set_task_status("t1", "blocked", "spec deviation: missing tests")

        blocked = project.get_blocked_tasks()
        assert len(blocked) == 1
        assert blocked[0].failure_reason == "spec deviation: missing tests"

    def test_subagent_runs(self, project):
        project.create_task("parent", 1, "Executor")
        r1 = project.create_subagent_run("run_1", "parent", "code_agent")
        assert r1 == "run_1"

        project.finish_subagent_run("run_1", "done", output_summary="generated 5 files")
        failed = project.get_failed_subagent_runs()
        assert len(failed) == 0
