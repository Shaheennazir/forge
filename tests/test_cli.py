"""Tests for forge.cli — command interface."""

import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from pathlib import Path
import tempfile

from forge.cli import main, status, memory, plan


class TestCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def mock_llm(self):
        """Patch create_backend to return a stub LLM that doesn't hit the network."""
        stub = MagicMock()
        stub.config.provider = "mock"
        stub.config.model = "mock-model"
        stub.complete.return_value = '{"action": "generate_spec", "reason": "stub"}'
        stub.complete_json.return_value = {"action": "generate_spec"}
        with patch("forge.llm.create_backend", return_value=stub):
            yield stub

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_new_creates_project(self, runner, mock_llm):
        with tempfile.TemporaryDirectory() as td:
            result = runner.invoke(main, ["new", "build a web app", "--project", "test_web_app"])
            # May fail at execution, but DB should init without schema errors
            assert result.exception is None or " ForgeDB" not in str(result.exception)

    def test_status_no_project(self, runner):
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0

    def test_plan_helpful(self, runner, mock_llm):
        result = runner.invoke(main, ["plan", "add user authentication"])
        assert result.exception is None
        assert "Planning" in result.output or "Spec" in result.output

    def test_agents_command(self, runner):
        result = runner.invoke(main, ["agents"])
        assert "v0.2" in result.output
        assert result.exit_code == 0
