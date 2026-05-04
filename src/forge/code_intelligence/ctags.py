"""
forge.code_intelligence.ctags — ctags integration for symbol indexing.

ctags is a language-agnostic binary. We call it via subprocess with JSON output
when available (--output-format=json), falling back to line parsing.

Usage:
    tags = CTags.discover("/path/to/project")
    for sym in tags.search("my_function"):
        print(sym.name, sym.file, sym.line)
"""

from __future__ import annotations

import json
import re
import shutil
import structlog
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


@dataclass
class Symbol:
    """A code symbol discovered by ctags."""

    name: str
    file: str
    line: int
    kind: str  # function, class, variable, etc.
    scope: str = ""  # e.g. "ClassName" for methods


class CTags:
    """
    ctags wrapper for symbol indexing.

    Tries:
      1. universal-ctags (ctags --output-format=json)
      2. exuberant-ctags / exctags (basic fields only)

    Agents use this to get a language-agnostic symbol index for navigation,
    rename refactoring, and cross-reference lookups.
    """

    def __init__(self, ctags_bin: str = "ctags"):
        self._bin = ctags_bin

    @classmethod
    def discover(cls, root: str | Path) -> Optional["CTags"]:
        """
        Find the best-available ctags binary and return a CTags instance.
        Returns None if ctags is not installed.
        """
        for bin_name in ("ctags", "uctags", "exctags", "tags"):
            path = shutil.which(bin_name)
            if path:
                # Verify it actually works
                try:
                    result = subprocess.run(
                        [path, "--version"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0:
                        log.info("ctags.found", binary=path, version=result.stdout.splitlines()[0])
                        return cls(bin_name)
                except Exception:
                    pass
        log.warning("ctags.not_found")
        return None

    def index(
        self,
        paths: list[str | Path],
        *,
        recursive: bool = True,
        languages: Optional[list[str]] = None,
    ) -> list[Symbol]:
        """
        Run ctags on the given paths and return all discovered symbols.

        Uses JSON output when available for maximum structure.
        Falls back to line-parsed output.
        """
        args = [
            self._bin,
            "--output-format=json" if self._supports_json() else "--output-format=euctags",
            "-R" if recursive else "",
            "--fields=+K+f+n",
            "--excmd=number",
            "--sort=no",  # preserve source order
        ]

        if languages:
            args.append(f"--languages={'+'.join(languages)}")

        # Add paths
        args.extend(str(p) for p in paths)

        # Filter empty strings
        args = [a for a in args if a]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            log.error("ctags.binary_missing", binary=self._bin)
            return []
        except subprocess.TimeoutExpired:
            log.error("ctags.timeout", paths=paths)
            return []

        if result.returncode != 0 and result.stderr:
            log.warning("ctags.stderr", stderr=result.stderr[:200])

        return self._parse_output(result.stdout)

    def search(
        self,
        root: str | Path,
        name: str,
        *,
        languages: Optional[list[str]] = None,
    ) -> list[Symbol]:
        """
        Search for symbols matching `name` in the project tree.

        Equivalent to running: ctags -R && grep name
        But uses ctags --languages to filter first.
        """
        return self.index([root], languages=languages)

    # ── Private ─────────────────────────────────────────────────────────────────

    def _supports_json(self) -> bool:
        """Check if this ctags binary supports JSON output."""
        try:
            result = subprocess.run(
                [self._bin, "--output-format=json", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_output(self, stdout: str) -> list[Symbol]:
        """Parse ctags stdout into Symbol objects."""
        if not stdout.strip():
            return []

        # Try JSON first
        try:
            return self._parse_json(stdout)
        except Exception:
            pass

        # Fall back to line format
        return self._parse_lines(stdout)

    def _parse_json(self, stdout: str) -> list[Symbol]:
        """Parse JSON lines format from universal-ctags."""
        symbols = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            name = entry.get("name", "")
            file_path = entry.get("path", entry.get("filename", ""))
            line_no = entry.get("line", 0)
            kind = entry.get("kind", "")
            scope = ""

            # scope field may be nested
            scope_parts = entry.get("scope", "")
            if isinstance(scope_parts, dict):
                scope = scope_parts.get("scope", "")
            elif isinstance(scope_parts, str):
                scope = scope_parts

            if name and file_path:
                symbols.append(Symbol(
                    name=name,
                    file=file_path,
                    line=line_no,
                    kind=kind,
                    scope=scope,
                ))

        return symbols

    def _parse_lines(self, stdout: str) -> list[Symbol]:
        """Parse line-based ctags output (kind letter prefix)."""
        symbols = []
        # Pattern: name<TAB>file<TAB>line<TAB>kind:
        # e.g. my_func\t/home/file.py\t/^def my_func/$;\tfunction
        pattern = re.compile(
            r"^([^\t]+)\t([^\t]+)\t(\d+)\t([^\t]+)"
        )
        for line in stdout.splitlines():
            m = pattern.match(line)
            if not m:
                continue
            name, file_path, line_no, kind = m.groups()
            # Strip kind prefix letters (e.g. f: → function)
            kind_clean = re.sub(r"^[a-z]+:", "", kind).strip()
            symbols.append(Symbol(
                name=name,
                file=file_path,
                line=int(line_no),
                kind=kind_clean or "unknown",
            ))
        return symbols
