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

log = structlog.get_logger(__name__)

CODER_SYSTEM = """You are a code execution agent.

Your job: implement a feature end-to-end given a failing test suite and a rule set.
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

        # 3. Run tests to see what's failing
        test_result = self._run_tests()
        yield {"type": "test_result", **test_result}

        # 4. Iterative improvement loop
        attempts = 0
        max_attempts = 5

        while test_result.get("failed", 0) > 0 and attempts < max_attempts:
            attempts += 1
            log.info("coder.iteration", attempt=attempts, failing=test_result.get("failed"))

            # Ask LLM to fix the failing tests
            fix_prompt = f"""The tests are failing. Fix the app.py to make them pass.

Current app.py:
{app_scaffold}

Test output:
{json.dumps(test_result, indent=2)}

Failing tests: {test_result.get('failed_names', [])}

Rewrite app.py with the fixes. Output the complete new file content.
"""
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
        """Run pytest with JSON output. Returns parsed result."""
        import subprocess

        result = subprocess.run(
            ["python", "-m", "pytest", str(self.workdir / "test_product.py"), "--json-report", "--json-report-file=/tmp/pytest_report.json"],
            capture_output=True,
            text=True,
            cwd=self.workdir,
        )

        # Try to read JSON report
        try:
            with open("/tmp/pytest_report.json") as f:
                report = json.load(f)
            passed = report.get("summary", {}).get("passed", 0)
            failed = report.get("summary", {}).get("failed", 0)
            failed_names = [
                r["nodeid"]
                for r in report.get("results", [])
                if r["outcome"] == "failed"
            ]
        except Exception:
            # Fallback: parse pytest output
            passed = 0
            failed = 0
            failed_names = []
            for line in result.stdout.splitlines():
                if "PASSED" in line:
                    passed += 1
                elif "FAILED" in line:
                    failed += 1
                    failed_names.append(line.strip())

        return {
            "passed": passed,
            "failed": failed,
            "failed_names": failed_names,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
        }
