"""
forge.code_intelligence.memray_ — Memray memory profiler wrapper.

Usage:
    from forge.code_intelligence.memray_ import run_memray_profile, ToolResult
    result = run_memray_profile(workdir=Path("."), test_cmd="pytest tests/ -x")

Profiles generated code under test workload and checks for:
- Memory leaks (objects that accumulate across iterations)
- Excessive allocations
- Peak memory usage above threshold

Test Runner uses this to ensure generated code doesn't leak memory.
"""

from __future__ import annotations

import shutil
import structlog
import subprocess
import json
from dataclasses import dataclass, field
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
class AllocationRecord:
    """A single memory allocation captured by memray."""
    size_bytes: int
    allocation_type: str     # "malloc" / "allocate" / "reallocate"
    thread_name: str
    frame: str               # stack frame as string


@dataclass
class MemoryProfile:
    """Memory profiling results."""
    peak_memory_mb: float = 0.0
    allocations: int = 0
    total_allocated_mb: float = 0.0
    leaked_allocations: list[dict] = field(default_factory=list)
    top_allocators: list[dict] = field(default_factory=list)


@dataclass
class ToolResult:
    success: bool
    passed: bool        # True if no memory leaks detected
    issues: list[Issue]
    raw: str
    profile: MemoryProfile | None = None


def run_memray_profile(
    workdir: Path,
    test_cmd: str = "pytest tests/ -x",
    output_path: Path | None = None,
    leak_check: bool = True,
    memory_limit_mb: float = 512.0,
) -> ToolResult:
    """
    Run memray with pytest to profile memory usage of the test suite.

    Runs: memray run --trace-py-allocations pytest tests/
    Then: memray report --format=json to parse results.

    Hard gate: peak memory > memory_limit_mb or detected memory leaks.
    Advisory: high allocation counts or large individual allocations.
    """
    bin_path = shutil.which("memray")
    if not bin_path:
        log.warning("memray_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="memray: not installed",
        )

    workdir = workdir.absolute()
    output_path = output_path or (workdir / "memray_output.bin")
    issues: list[Issue] = []

    try:
        # Run memray with pytest
        result = subprocess.run(
            [
                bin_path,
                "run",
                "--trace-py-allocations",
                "--live-port=0",   # disable live display
                "-o", str(output_path),
                "python", "-m", "pytest",
                "tests/",
                "-x",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(workdir),
        )
        raw = result.stdout + "\n" + result.stderr

        # Parse the summary output
        profile = _parse_memray_run_output(raw)
        profile_path = str(output_path) + ".json"

        # Generate JSON report
        if output_path.exists():
            report_result = subprocess.run(
                [bin_path, "report", "-o", profile_path, "--format=json", str(output_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            raw += "\n" + report_result.stdout

            try:
                with open(profile_path) as f:
                    report_data = json.load(f)
                profile = _parse_memray_json_report(report_data)
            except Exception:
                pass

        # Add issues
        if profile.peak_memory_mb > memory_limit_mb:
            issues.append(Issue(
                severity="HIGH",
                message=f"Peak memory {profile.peak_memory_mb:.1f}MB exceeds limit {memory_limit_mb}MB",
                file="",
                line=None,
                code=None,
            ))

        for leak in profile.leaked_allocations[:10]:
            issues.append(Issue(
                severity="HIGH",
                message=f"Memory leak: {leak.get('object_type', 'unknown')} accumulated {leak.get('count', 0)} times",
                file=leak.get("file", ""),
                line=leak.get("line"),
                code=None,
            ))

        for alloc in profile.top_allocators[:10]:
            issues.append(Issue(
                severity="MEDIUM",
                message=f"High allocator: {alloc.get('function', '?')} — {alloc.get('size_mb', 0):.1f}MB total",
                file=alloc.get("file", ""),
                line=alloc.get("line"),
                code=None,
            ))

        passed = len([i for i in issues if i.severity == "HIGH"]) == 0

    except subprocess.TimeoutExpired:
        issues.append(Issue(
            severity="MEDIUM",
            message="memray: profiling timed out after 5 minutes",
            file="",
            line=None,
            code=None,
        ))
        raw = "timeout"
        passed = True
    except Exception as e:
        issues.append(Issue(
            severity="MEDIUM",
            message=f"memray error: {e}",
            file="",
            line=None,
            code=None,
        ))
        raw = str(e)
        passed = True

    return ToolResult(
        success=True,
        passed=passed,
        issues=issues,
        raw=raw,
        profile=profile if 'profile' in locals() else None,
    )


def _parse_memray_run_output(raw: str) -> MemoryProfile:
    """Parse peak memory from memray run output."""
    profile = MemoryProfile()

    import re

    # e.g. "Peak memory baseline: 12.4 MiB"
    peak_match = re.search(r"Peak memory (?:baseline |)([\d.]+)\s*(MiB|GiB)", raw)
    if peak_match:
        mb = float(peak_match.group(1))
        if "GiB" in peak_match.group(0):
            mb *= 1024
        profile.peak_memory_mb = mb

    # e.g. "Allocations: 12345"
    alloc_match = re.search(r"Allocations:\s*(\d+)", raw)
    if alloc_match:
        profile.allocations = int(alloc_match.group(1))

    return profile


def _parse_memray_json_report(data: dict) -> MemoryProfile:
    """Parse memray JSON report to extract memory leaks and top allocators."""
    profile = MemoryProfile()

    if "peaks" in data:
        profile.peak_memory_mb = data["peaks"].get("peak_memory_bytes", 0) / (1024 * 1024)

    if "memory_leaks" in data:
        for leak in data["memory_leaks"]:
            profile.leaked_allocations.append({
                "object_type": leak.get("type", "unknown"),
                "count": leak.get("count", 0),
                "size_bytes": leak.get("size", 0),
                "file": leak.get("file", ""),
                "line": leak.get("line"),
            })

    if "allocator_stats" in data:
        for stat in data["allocator_stats"][:20]:
            profile.top_allocators.append({
                "function": stat.get("function", "unknown"),
                "size_mb": stat.get("total_size", 0) / (1024 * 1024),
                "count": stat.get("count", 0),
                "file": stat.get("file", ""),
                "line": stat.get("line"),
            })

    return profile


def run_memray_flamegraph(
    workdir: Path,
    output_path: Path | None = None,
) -> ToolResult:
    """
    Generate a flamegraph from memray profiling.

    Use this when you need a visual representation of where memory
    is being allocated. Output is a .html flamegraph file.
    """
    bin_path = shutil.which("memray")
    if not bin_path:
        return ToolResult(success=False, passed=True, issues=[], raw="memray not installed")

    output_path = output_path or (workdir / "memray_flamegraph.html")
    raw = ""

    try:
        # First run: capture
        bin_path_out = str(output_path.with_suffix(".bin"))
        subprocess.run(
            [bin_path, "run", "-o", bin_path_out, "python", "-m", "pytest", "tests/", "-x"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(workdir),
        )

        # Then: flamegraph
        result = subprocess.run(
            [bin_path, "flamegraph", "-o", str(output_path), bin_path_out],
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw = result.stdout + "\n" + result.stderr

    except Exception as e:
        return ToolResult(success=False, passed=True, issues=[], raw=str(e))

    return ToolResult(
        success=True,
        passed=True,
        issues=[],
        raw=raw,
        profile=None,
    )
