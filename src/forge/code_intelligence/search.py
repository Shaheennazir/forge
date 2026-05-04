"""
forge.code_intelligence.search — semgrep integration for code analysis.

semgrep is a CLI + Python API for structured code search and pattern matching.
Agents use this to find code patterns, enforce rules, and analyze code quality.

Usage:
    search = CodeSearch()
    results = search.scan(["src/"], pattern="TODO", languages=["python"])
    for r in results:
        print(r.file, r.line, r.extra["message"])

    # Pattern-based search
    results = search.scan(["src/"], pattern="$F = $X ** 2", languages=["python"])
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Lazy import — semgrep is optional (not installed by default)
_semgrep = None


def _get_semgrep():
    global _semgrep
    if _semgrep is None:
        try:
            from semgrep import SemgrepSession, semgrep_main
            _semgrep = (SemgrepSession, semgrep_main)
        except ImportError:
            pass
    return _semgrep


@dataclass
class SearchResult:
    """A single semgrep match result."""

    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    language: str
    pattern: str
    message: str
    severity: str  # ERROR, WARNING, INFO, NOTE
    check_id: str = ""  # rule ID


class CodeSearch:
    """
    semgrep-powered code search and analysis.

    Usage:
        search = CodeSearch()
        # Text search
        results = search.grep(["src/"], "django.conf")
        # Pattern search (semgrep pattern syntax)
        results = search.scan(["src/"], "$F = $X ** 2")
    """

    def __init__(self, config: Optional[str] = "auto"):
        """
        Args:
            config: semgrep config. Defaults to "auto" (rules from semgrep.dev).
                    Use "no-backing-tests" for faster runs without test rules.
                    Pass a path like "rules/" for local rule directories.
        """
        self.config = config

    def scan(
        self,
        paths: list[str | Path],
        pattern: str,
        *,
        languages: Optional[list[str]] = None,
        timeout: int = 30,
    ) -> list[SearchResult]:
        """
        Run semgrep pattern matching on the given paths.

        Args:
            paths: Directories or files to scan.
            pattern: semgrep pattern (e.g. "django.conf.settings").
            languages: List of languages to restrict (e.g. ["python", "typescript"]).
            timeout: Max seconds per file.

        Returns:
            List of SearchResult objects.
        """
        sg = _get_semgrep()
        if sg is None:
            log.warning("semgrep.not_installed")
            return []

        _, semgrep_main = sg
        results = []

        try:
            output = semgrep_main.main(
                command=["semgrep", "scan"],
                target=paths,
                config=self.config,
                no_git_ignore=False,
                no_ignore=False,
                quiet=True,
                text_output=True,
                json_output=True,
                timeout=timeout,
                lang_filter=languages,
            )
        except Exception as e:
            log.warning("semgrep.scan_failed", error=str(e))
            return []

        # semgrep_main returns (raw_output, passed)
        if isinstance(output, tuple):
            raw, _ = output
        else:
            raw = output

        try:
            import json
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            log.warning("semgrep.json_parse_failed")
            return []

        for r in data.get("results", []):
            results.append(SearchResult(
                file=r.get("path", ""),
                line=r.get("start", {}).get("line", 0),
                column=r.get("start", {}).get("col", 0),
                end_line=r.get("end", {}).get("line", 0),
                end_column=r.get("end", {}).get("col", 0),
                language=r.get("language", ""),
                pattern=r.get("check_id", pattern),
                message=r.get("extra", {}).get("message", ""),
                severity=r.get("extra", {}).get("severity", "WARNING"),
                check_id=r.get("check_id", ""),
            ))

        return results

    def grep(
        self,
        paths: list[str | Path],
        query: str,
        *,
        languages: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """
        Simple text/regex search across files (alias: semgrep --pattern).

        This is equivalent to ripgrep but powered by semgrep (so it understands
        language structure and ignores test files by default).

        For pure regex across file names and paths, use the ripgrep binary directly.
        """
        # semgrep uses "pattern:" prefix to distinguish from config rules
        return self.scan(paths, query, languages=languages)

    def enforce_rule(
        self,
        paths: list[str | Path],
        rule_id: str,
        *,
        languages: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """
        Enforce a specific semgrep rule (by rule ID) on paths.

        Example rule_ids:
          - "python.lang.correctness.eqeq"  (yoda conditions)
          - "typescript.lang.security.detect-xss"
        """
        return self.scan(paths, rule_id, languages=languages)

    def list_rules(self, category: Optional[str] = None) -> list[str]:
        """
        List available semgrep rules. Queries the registry.

        Args:
            category: Filter by category (e.g. "correctness", "security", "style").
        """
        sg = _get_semgrep()
        if sg is None:
            return []
        _, semgrep_main = sg

        try:
            # semgrep --dump- rules
            output = semgrep_main.main(
                command=["semgrep", "scan"],
                target=[],
                config="auto",
                quiet=True,
                text_output=True,
                json_output=True,
            )
            if isinstance(output, tuple):
                raw, _ = output
            else:
                raw = output
            import json
            data = json.loads(raw) if isinstance(raw, str) else raw
            rules = data.get("rules", [])
            if category:
                rules = [r for r in rules if r.get("metadata", {}).get("category") == category]
            return [r.get("id", "") for r in rules]
        except Exception:
            return []
