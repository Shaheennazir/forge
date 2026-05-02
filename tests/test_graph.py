"""Tests for forge.graph — Directed graph engine."""

import pytest
import tempfile
from pathlib import Path

from forge.db import ForgeDB
from forge.graph import (
    ForgeGraph, GraphRunner, GraphNode, GraphEdge,
    NodeStatus, run_orchestrator, run_spec_gen, run_executor, run_review_gate,
)


class TestForgeGraph:
    @pytest.fixture
    def tmp_g(self):
        with tempfile.TemporaryDirectory() as td:
            db = ForgeDB(project_id="graph_test", db_path=Path(td) / "g.db")
            db.upsert_project("Graph Test")
            g = ForgeGraph("graph_test", db)
            g.build_initial_graph(spec_version=1)
            yield g, db
            db.close()

    def test_initial_graph_structure(self, tmp_g):
        g, db = tmp_g
        assert set(g.nodes.keys()) == {"orchestrator", "spec_gen", "executor", "review_gate"}
        assert len(g.edges) >= 6  # normal + review_pass + review_fail + failures

        # orchestrator → spec_gen → executor → review_gate main path
        assert "spec_gen" in g.successors("orchestrator")
        assert "executor" in g.successors("spec_gen")
        assert "review_gate" in g.successors("executor")

        # review_gate has both pass and fail edges
        rg_edges = g.get_outgoing_edges("review_gate")
        edge_types = {e.edge_type for e in rg_edges}
        assert "review_pass" in edge_types
        assert "review_fail" in edge_types

    def test_node_status_update(self, tmp_g):
        g, db = tmp_g
        g.update_node_status("spec_gen", NodeStatus.RUNNING)
        assert g.get_node("spec_gen").status == NodeStatus.RUNNING

        g.update_node_status("spec_gen", NodeStatus.BLOCKED, "missing input schema")
        assert g.get_node("spec_gen").status == NodeStatus.BLOCKED
        assert g.get_node("spec_gen").failure_reason == "missing input schema"

        # DB reflects it
        t = db.get_task("spec_gen")
        assert t.status == "blocked"
        assert t.failure_reason == "missing input schema"

    def test_spec_gen_runner(self, tmp_g):
        g, db = tmp_g
        stm = {}
        # First call on fresh DB (no prior spec)
        out1 = run_spec_gen(g, db, "spec_gen", stm, prompt="build a CLI tool")
        assert out1["spec_version"] == 2, "first spec should be version 2"

        # Second call continues from existing spec
        out2 = run_spec_gen(g, db, "spec_gen", stm, prompt="add tests")
        assert out2["spec_version"] == 3, "third spec should be version 3"
        assert "SPEC.md" in out2["spec_md"]
        assert g.get_node("spec_gen").status == NodeStatus.DONE

    def test_executor_runner(self, tmp_g):
        g, db = tmp_g
        stm = {}
        out = run_executor(g, db, "executor", stm, spec_md="# SPEC")
        assert "files" in out
        assert len(out["files"]) > 0
        assert g.get_node("executor").status == NodeStatus.DONE

    def test_review_gate_pass(self, tmp_g):
        g, db = tmp_g
        stm = {"spec_md": "# SPEC"}
        exec_out = {"files": [{"path": "main.py"}]}
        out = run_review_gate(g, db, "review_gate", stm, exec_out)
        assert out["decision"] == "pass"

    def test_review_gate_fail(self, tmp_g):
        g, db = tmp_g
        # Force a fail by patching the review function temporarily
        import forge.graph as fg
        orig = fg._review_spec_compliance
        fg._review_spec_compliance = lambda *a, **kw: {"pass": False, "reason": "test failure"}
        try:
            stm = {"spec_md": "# SPEC"}
            out = run_review_gate(g, db, "review_gate", stm, {"files": []})
            assert out["decision"] == "fail"
            assert g.get_node("review_gate").status == NodeStatus.BLOCKED
        finally:
            fg._review_spec_compliance = orig

    def test_graph_runner_happy_path(self, tmp_g):
        g, db = tmp_g
        runner = GraphRunner(g, db)
        result = runner.run(
            entry_node="orchestrator",
            prompt="build a CLI tool",
            continue_session=False,
        )
        assert "orchestrator" in result["visited"]
        assert "spec_gen" in result["visited"]
        assert "executor" in result["visited"]
        assert "review_gate" in result["visited"]
        assert result["final_node"] == "review_gate"

    def test_graph_runner_blocks_on_failure(self, tmp_g):
        g, db = tmp_g
        # Force spec_gen to fail
        g.update_node_status("spec_gen", NodeStatus.FAILED, "API error")
        runner = GraphRunner(g, db)
        result = runner.run(entry_node="orchestrator", prompt="x", continue_session=False)
        # Should stop at orchestrator's failure handling
        assert "orchestrator" in result["visited"]
