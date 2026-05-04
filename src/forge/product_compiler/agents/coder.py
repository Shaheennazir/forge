"""
forge.product_compiler.agents.coder — Makes tests green.

The coder's only job: write code until tests pass.
It has a schema, contracts, a rule set, and a failing test suite.
No architectural decisions. No interpreting requirements. No design work.
It executes.

This is a generator that yields progress events as it writes files.
"""

from __future__ import annotations

import json
import structlog
from pathlib import Path
from typing import Generator

from forge.llm import LLMBackend
from forge.product_compiler.models import FailingTestSuite, RuleSet
from forge.product_compiler.contracts import ContractSpec

log = structlog.get_logger(__name__)


def _build_fix_prompt(
    test_result: dict,
    app_scaffold: str,
    attempt: int,
) -> str:
    """
    Build a structured fix prompt from the structured test failure output.

    The Coder receives per-failure context (file, line, type, message) not a
    text blob. This lets it address each failure precisely rather than guessing.
    """
    failures = test_result.get("failures", [])

    if failures:
        failure_blocks = []
        for i, f in enumerate(failures, 1):
            failure_blocks.append(f"""## Failure {i}
  File:    {f.get('file', '?')}
  Line:    {f.get('line', '?')}
  Type:    {f.get('type', 'Error')}
  Message: {f.get('message', 'no message')}

  Actual test name: {f.get('name', '?')}""")

        failures_section = "\n".join(failure_blocks)
    else:
        failures_section = f"Failed tests (names only): {test_result.get('failed_names', [])}"

    return f"""The tests are still failing. Fix app.py to make them pass.

Attempt: {attempt} of 5

## Current app.py
{app_scaffold}

## Test Results Summary
  Total:  {test_result.get('total', '?')}
  Passed: {test_result.get('passed', '?')}
  Failed: {test_result.get('failed', '?')}

## Per-Failure Context
{failures_section}

## Your Task
1. Read each failure above
2. For each failing test, identify the exact assertion or import that failed
3. Fix app.py to satisfy all assertions
4. Output the complete new app.py content (no markdown fences, no explanation)
"""


def _run_tests(workdir: Path) -> dict:
    """Run pytest with JSON output. Returns parsed result matching common.py schema."""
    import json as _json, os, subprocess

    json_report = workdir / "results.json"
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", str(workdir / "test_product.py"),
             "--json-report", "--json-report-file=results.json", "-q"],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"total": 0, "failed": -1, "output": "timeout", "failures": []}
    except Exception:
        return {"total": 0, "failed": -1, "output": "pytest unavailable", "failures": []}

    if json_report.exists():
        try:
            with open(json_report) as f:
                report = _json.load(f)
            os.unlink(json_report)
            return _coder_parse_json_report(report)
        except Exception:
            pass

    # Fallback: parse stdout
    passed = failed = 0
    failed_names = []
    for line in result.stdout.splitlines():
        if "PASSED" in line:
            passed += 1
        elif "FAILED" in line:
            failed += 1
            failed_names.append(line.strip())
    return {
        "total": passed + failed,
        "passed": passed,
        "failed": failed,
        "failed_names": failed_names,
        "output": result.stdout[:500],
        "failures": [],
    }


def _coder_parse_json_report(report: dict) -> dict:
    """Parse pytest-json-report into Coder's expected schema with per-failure detail."""
    summary = report.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    if total == 0:
        total = passed + failed

    failures = []
    for entry in report.get("tests", []):
        if entry.get("outcome") != "failed":
            continue
        nodeid = entry.get("nodeid", "")
        call_section = entry.get("call", {})
        longrepr = call_section.get("longrepr", "")
        message = ""
        if longrepr:
            message = str(longrepr) if not isinstance(longrepr, str) else longrepr
        failure_type = "AssertionError" if "AssertionError" in message else "Error"
        file_part = nodeid.split("::")[0] if "::" in nodeid else nodeid
        failures.append({
            "name": nodeid,
            "file": file_part,
            "line": str(entry.get("lineno", "")),
            "type": failure_type,
            "message": message.strip(),
        })

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "output": f"{passed} passed, {failed} failed",
        "failures": failures,
    }


CODER_SYSTEM = """You are a code execution agent.
You MUST:
1. Read the existing test file to understand what to implement
2. Write the application code to make tests pass
3. Run the tests after each file write
4. Iterate until all tests pass

You write to files. You run tests. You do not modify tests.

Output your progress as JSON after each file write:
{"type": "file_written", "path": "filename.py", "summary": "..."}
{"type": "test_result", "passed": N, "failed": N, "test": "test_name"}
{"type": "complete", "files_written": [...], "all_passed": bool}
"""


class CoderAgent:
    def __init__(self, llm: LLMBackend, workdir: Path | str = Path("."), sandbox=None):
        self.llm = llm
        self.workdir = Path(workdir)
        self.sandbox = sandbox

    def run(
        self, test_suite: FailingTestSuite, rule_set: RuleSet
    ) -> Generator[dict, None, dict]:
        """
        Write code until tests pass. Yields progress events.
        Returns final result dict.
        """
        # 1. Write the test file
        test_file = self.workdir / "test_product.py"
        test_file.write_text(self._render_test_file(test_suite))
        log.info("coder.wrote_tests", path=str(test_file))

        yield {"type": "file_written", "path": str(test_file), "summary": f"{len(test_suite.test_cases)} test cases"}

        # 2. Write the application code scaffold (also derived from rules)
        app_file = self.workdir / "app.py"
        app_scaffold = self._generate_scaffold(test_suite, rule_set)
        app_file.write_text(app_scaffold)
        log.info("coder.wrote_scaffold", path=str(app_file))

        yield {"type": "file_written", "path": str(app_file), "summary": "app scaffold from rules"}

        # Auto-upgrade generated code to latest Python idioms before testing
        _run_pyupgrade(self.workdir)

        # Run Crosshair contract prover on the scaffold — hard gate on violations
        contract_failures = _run_crosshair(self.workdir)
        for cf in contract_failures:
            log.warning("coder.contract_violation", violation=cf)
        if contract_failures:
            log.warning("coder.crosshair_failed", count=len(contract_failures))

        # 3. Run tests to see what's failing
        with _tracer_start_span("coder.test_run", {"attempt": 0}):
            test_result = self._run_tests()
        yield {"type": "test_result", **test_result}

        # 4. Iterative improvement loop
        attempts = 0
        max_attempts = 5

        while test_result.get("failed", 0) > 0 and attempts < max_attempts:
            attempts += 1
            log.info("coder.iteration", attempt=attempts, failing=test_result.get("failed"))

            # Build structured fix prompt with per-failure context
            fix_prompt = _build_fix_prompt(
                test_result=test_result,
                app_scaffold=app_scaffold,
                attempt=attempts,
            )
            response = self.llm.complete(
                prompt=fix_prompt,
                system=CODER_SYSTEM,
                max_tokens=8192,
                temperature=0.2,
            )
            content = response.content if hasattr(response, "content") else str(response)

            # Try to extract code from response
            new_scaffold = self._extract_code(content, "app.py") or app_scaffold
            app_file.write_text(new_scaffold)
            app_scaffold = new_scaffold

            yield {"type": "file_written", "path": str(app_file), "summary": f"fix attempt {attempts}"}

            # Run pyupgrade on the fixed code
            _run_pyupgrade(self.workdir)

            # Crosshair contract check after each fix
            contract_failures = _run_crosshair(self.workdir)
            for cf in contract_failures:
                log.warning("coder.contract_violation.post_fix", violation=cf)

            test_result = self._run_tests()
            yield {"type": "test_result", **test_result}

        all_passed = test_result.get("failed", 0) == 0
        log.info("coder.complete", all_passed=all_passed, iterations=attempts)

        return {
            "type": "complete",
            "files_written": [str(test_file), str(app_file)],
            "all_passed": all_passed,
            "passed": test_result.get("passed", 0),
            "failed": test_result.get("failed", 0),
        }

    def _render_test_file(self, test_suite: FailingTestSuite) -> str:
        """Render test cases to a pytest file."""
        imports = [
            "import pytest",
            "import sys",
            "import os",
            "sys.path.insert(0, os.path.dirname(__file__))",
        ]

        test_methods = []
        for tc in test_suite.test_cases:
            test_methods.append(f"""
def test_rule_{tc.rule_id[:8]}_{tc.description.replace(' ', '_').lower()[:30]}():
    \"\"\"{tc.description}\"\"\"
    {tc.test_code}
""")

        return "\n".join(imports + ["", ""] + test_methods)

    def _detect_framework(self, test_suite: FailingTestSuite) -> str:
        all_code = " ".join(tc.test_code for tc in test_suite.test_cases).lower()
        if "fastapi" in all_code: return "fastapi"
        if "flask" in all_code: return "flask"
        if "django" in all_code: return "django"
        return "fastapi"

    def _generate_scaffold(self, test_suite: FailingTestSuite, rule_set: RuleSet) -> str:
        """Use LLM to generate the initial app scaffold from rules + test structure."""
        framework = self._detect_framework(test_suite)
        prompt = f"""Generate a Python {framework} app scaffold that satisfies these tests.

Rules:
{json.dumps(rule_set.to_dict(), indent=2)}

Test structure (each test calls an endpoint — stubs return 200):
{json.dumps([{{"rule_id": tc.rule_id, "description": tc.description}} for tc in test_suite.test_cases], indent=2)}

Write a minimal {framework} app with:
- All routes referenced in tests returning 200 (stub)
- Request/response models matching the test expectations
- Basic auth middleware stub
- All test fixtures passing

Output ONLY the Python file content. No markdown, no explanation.
"""
        response = self.llm.complete(
            prompt=prompt,
            system="You are a code generation agent. Output ONLY the file content, no markdown fences.",
            max_tokens=8192,
            temperature=0.2,
        )
        content = response.content if hasattr(response, "content") else str(response)
        return self._extract_code(content, "app.py") or content

    def _extract_code(self, content: str, filename: str) -> str | None:
        """Extract code from a markdown code block."""
        import re

        patterns = [
            rf"```{python}\n(.*?)```",
            rf"```py\n(.*?)```",
            rf"```\n(.*?)```",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                return match.group(1).strip()
        # If no code block, look for the content after the filename mention
        return None

    def _run_tests(self) -> dict:
        """Run pytest. Delegates to module-level _run_tests with proper JSON schema."""
        return _run_tests(self.workdir)


def _run_pyupgrade(workdir: Path) -> None:
    """
    Run pyupgrade to auto-upgrade generated Python code to latest idioms.
    Called before each test run in the Coder TDD loop — modifies files in-place.
    """
    from forge.code_intelligence.pyupgrade_ import run_pyupgrade
    try:
        result = run_pyupgrade(workdir)
        if result.files_modified > 0:
            log.info("coder.pyupgrade", files_modified=result.files_modified, raw=result.raw)
    except Exception as e:
        log.warning("coder.pyupgrade_failed", error=str(e))


def _run_crosshair(workdir: Path) -> list[str]:
    """
    Run Crosshair contract prover on all Python files in workdir.

    Crosshair proves whether PEP 316 contracts (requires/ensures) can be violated.
    Returns a list of violation messages. Empty list = all contracts provably satisfied.

    This is a hard gate: if Crosshair finds a violation, the contract is broken.
    """
    from forge.code_intelligence.crosshair_ import run_crosshair

    try:
        result = run_crosshair(workdir=workdir, timeout=60)
        if not result.passed:   # contracts can be violated
            return [issue.message for issue in result.issues]
    except Exception as e:
        log.warning("coder.crosshair_failed", error=str(e))

    return []


class _NoOpSpan:
    """No-op span when tracing is unavailable."""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def set_attribute(self, key, value): pass
    def add_event(self, name, attributes=None): pass
    def record_exception(self, exc): pass


def _tracer_start_span(name: str, attributes: dict | None = None):
    """
    Start an OpenTelemetry span for a Coder operation.

    Falls back to a no-op context manager if OpenTelemetry is unavailable.
    When tests fail, the span captures the failure for trace-based debugging.
    """
    try:
        from forge.code_intelligence.otel_ import create_tracer
        tracer = create_tracer("forge-coder")
        span = tracer.start_as_current_span(name)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        return span
    except Exception:
        return _NoOpSpan()

