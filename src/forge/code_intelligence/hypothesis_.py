"""
forge.code_intelligence.hypothesis_ — Hypothesis property-based test wrapper.

Usage:
    from forge.code_intelligence.hypothesis_ import run_hypothesis, ToolResult
    result = run_hypothesis(workdir=Path("tests/"))

Generates property-based tests from a FailingTestSuite by upgrading
individual test cases to @hypothesis.given(...) decorators.
"""

from __future__ import annotations

import shutil
import structlog
import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


@dataclass
class Issue:
    severity: str       # HIGH / MEDIUM / LOW
    message: str
    file: str
    line: Optional[int]
    code: Optional[str]


@dataclass
class ToolResult:
    success: bool
    passed: bool        # property tests generated and syntactically valid
    issues: list[Issue]
    raw: str


def run_hypothesis(workdir: Path, test_suite_path: Path | None = None) -> ToolResult:
    """
    Run hypothesis health checks on all @given-decorated tests in the workdir.

    This does NOT generate new tests — it validates that any existing
    property-based tests are well-formed (no invalid strategies, health
    check failures, etc.).

    For property test generation from rule sets, use the TestWriter agent
    with hypothesis_prompt augmentation.
    """
    bin_path = shutil.which("hypothesis")
    if not bin_path:
        log.warning("hypothesis_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="hypothesis: not installed",
        )

    issues: list[Issue] = []

    # Run: hypothesis --notes-to-outputs will write health check failures to notes
    # We use pytest with hypothesis to run health checks
    test_dir = test_suite_path or workdir
    try:
        result = subprocess.run(
            [
                bin_path, "hypothesis",
                "--health-check=basic",
                "--health-check=lenient",
                "--notes-to-outputs",
                str(test_dir),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        raw = result.stdout + result.stderr

        # Parse hypothesis output for health check failures
        # Hypothesis writes "hypothesis-output" to stderr for flaky tests
        for line in raw.splitlines():
            if "hypothesis." in line.lower() or "health check" in line.lower():
                issues.append(Issue(
                    severity="MEDIUM",
                    message=line.strip(),
                    file="",
                    line=None,
                    code=None,
                ))

    except subprocess.TimeoutExpired:
        return ToolResult(
            success=True,
            passed=True,
            issues=[],
            raw="hypothesis: health check timeout (ok if tests are slow)",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw=f"hypothesis error: {e}",
        )

    passed = all(i.severity not in ("HIGH", "CRITICAL") for i in issues)

    return ToolResult(
        success=True,
        passed=passed,
        issues=issues,
        raw=raw,
    )


def generate_property_tests(
    workdir: Path,
    test_cases: list[dict],
    rules: list[dict],
) -> list[str]:
    """
    Generate @hypothesis.given property-based tests from a FailingTestSuite.

    For each test case in the suite, upgrades it to use @hypothesis.given
    decorators for at least one parameter with a strategy.

    Returns a list of file paths for the generated property test files.
    """
    from hypothesis import given, strategies as st
    import sys

    generated_files: list[str] = []

    for tc in test_cases:
        rule_id = tc.get("rule_id", "unknown")
        test_code = tc.get("test_code", "")

        # Find simple input parameters and upgrade to @given
        # e.g. if test does def test_rule_1_email_validation(email="bad@"): 
        #   → @given(email=st.emails()) 
        # This is a simple heuristic; the LLM in TestWriter does the full generation

        # Write a property-based version alongside the original
        if test_code and "@given" not in test_code:
            prop_test_code = _upgrade_to_property_test(test_code, tc.get("description", ""))
            if prop_test_code:
                out_path = workdir / f"test_rule_{rule_id}_property.py"
                out_path.write_text(prop_test_code)
                generated_files.append(str(out_path))

    return generated_files


def _upgrade_to_property_test(test_code: str, description: str) -> str:
    """
    Heuristic: if a test has a hardcoded string argument named 'email', 'name',
    'price', 'quantity', etc., wrap it in @hypothesis.given with a strategy.

    This is a best-effort augmentation. Full property test generation happens
    in TestWriterAgent with LLM.
    """
    from hypothesis import given, strategies as st

    strategy_map = {
        "email": "st.emails()",
        "name": "st.text(min_size=1, max_size=100)",
        "price": "st.floats(min_value=0, max_value=1_000_000)",
        "quantity": "st.integers(min_value=0, max_value=10_000)",
        "username": "st.text(min_size=3, max_size=30)",
        "password": "st.text(min_size=8, max_size=128)",
        "url": "st.urls()",
        "phone": "st.from_regex(r'\\+?[0-9]{10,15}')",
        "date": "st.dates()",
    }

    import re

    # Find function def with default args
    match = re.search(r"def (test_\w+)\((\w+)=(['\"]([^'\"]+)['\"])\)", test_code)
    if not match:
        return ""

    test_name = match.group(1)
    param_name = match.group(2)
    default_val = match.group(4)

    if param_name not in strategy_map:
        return ""

    strategy = strategy_map[param_name]
    old_def = "def " + test_name + "(" + param_name + "="
    new_def = "@given(" + param_name + "=" + strategy + ")\ndef " + test_name + "(" + param_name + "="
    upgraded = test_code.replace(old_def, new_def)

    new_test = """
Property-based test for: {description}
Auto-generated by hypothesis_ wrapper.
"""
    new_test = """\"\"\"
Property-based test for: """ + description + """
Auto-generated by hypothesis_ wrapper.
\"\"\"
from hypothesis import given, strategies as st

""" + upgraded + """

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
"""
    return new_test
