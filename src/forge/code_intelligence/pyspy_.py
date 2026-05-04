"""
forge.code_intelligence.pyspy_ — py-spy sampling profiler wrapper.

Usage:
    from forge.code_intelligence.pyspy_ import run_pyspy_profile, ToolResult
    result = run_pyspy_profile(pid=12345, duration=30, output_path="profile.svg")

Profiles a running Python process with near-zero overhead.
py-spy attaches to any running Python process without restarting it.
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
class ProfilerResult:
    """Results from a py-spy profiling session."""
    duration_seconds: float = 0.0
    samples_collected: int = 0
    top_functions: list[dict] = field(default_factory=list)  # [{fn, file, line, samples, pct}]
    blocked_functions: list[dict] = field(default_factory=list)  # functions that took >10% of time


@dataclass
class ToolResult:
    success: bool
    passed: bool        # True if no function exceeds time threshold
    issues: list[Issue]
    raw: str
    profile: ProfilerResult | None = None


def run_pyspy_profile(
    pid: int | None = None,
    workdir: Path | None = None,
    test_cmd: str | None = None,
    duration: int = 30,
    output_path: Path | None = None,
    blocked_threshold_pct: float = 10.0,    # flag functions taking >10% of time
) -> ToolResult:
    """
    Run py-spy to profile Python code.

    Two modes:
    - Attach to a running PID
    - Spawn a new process (test_cmd) and profile it

    Generates a speedscope-format profile that can be viewed in VS Code
    or uploaded to speedscope.app.
    """
    bin_path = shutil.which("py-spy")
    if not bin_path:
        log.warning("pyspy_.not_installed")
        return ToolResult(
            success=False,
            passed=True,
            issues=[],
            raw="py-spy: not installed",
        )

    output_path = output_path or Path("profile.speedscope.json")
    issues: list[Issue] = []
    raw = ""

    try:
        cmd = [
            bin_path,
            "record",
            "--duration", str(duration),
            "--format", "speedscope",
            "-o", str(output_path),
        ]

        if pid:
            cmd.extend(["--pid", str(pid)])
        elif test_cmd:
            # Spawn a subprocess and profile it
            cmd.extend(["--", "python", "-m", "pytest", "tests/", "-x"])
        else:
            return ToolResult(
                success=False,
                passed=True,
                issues=[],
                raw="py-spy: must provide either pid or test_cmd",
            )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 30,
            cwd=str(workdir.absolute()) if workdir else None,
        )
        raw = result.stdout + "\n" + result.stderr

        # Parse the speedscope JSON
        profile = _parse_speedscope(output_path)

        # Flag slow functions
        for fn in profile.top_functions:
            if fn.get("pct", 0) > blocked_threshold_pct:
                issues.append(Issue(
                    severity="MEDIUM",
                    message=f"Slow function: {fn.get('fn', '?')} took {fn.get('pct', 0):.1f}% of time ({fn.get('samples', 0)} samples)",
                    file=fn.get("file", ""),
                    line=fn.get("line"),
                    code=None,
                ))

    except subprocess.TimeoutExpired:
        issues.append(Issue(
            severity="MEDIUM",
            message=f"py-spy: timed out after {duration + 30}s",
            file="",
            line=None,
            code=None,
        ))
        raw = "timeout"
    except Exception as e:
        issues.append(Issue(
            severity="MEDIUM",
            message=f"py-spy error: {e}",
            file="",
            line=None,
            code=None,
        ))
        raw = str(e)

    return ToolResult(
        success=True,
        passed=len(issues) == 0,
        issues=issues,
        raw=raw,
        profile=profile if 'profile' in locals() else None,
    )


def _parse_speedscope(path: Path) -> ProfilerResult:
    """Parse a speedscope JSON file to extract top functions."""
    profile = ProfilerResult()

    try:
        with open(path) as f:
            data = json.load(f)

        # Speedscope format: frames are indexed; profiles reference frame indices
        frames = data.get("shared", {}).get("frames", [])
        profile_name = data.get("profiles", [{}])[0].get("type", "")

        if profile_name == "evented":
            # Sample-based profiling
            samples = data.get("profiles", [{}])[0].get("sampleInfos", [])
            fn_counts: dict[int, int] = {}
            total = 0

            for sample in samples:
                for s in sample:
                    idx = s.get("frame", 0)
                    fn_counts[idx] = fn_counts.get(idx, 0) + 1
                    total += 1

            for idx, count in sorted(fn_counts.items(), key=lambda x: -x[1])[:20]:
                frame = frames[idx] if idx < len(frames) else {}
                pct = (count / total * 100) if total > 0 else 0
                profile.top_functions.append({
                    "fn": frame.get("name", "?"),
                    "file": frame.get("file", ""),
                    "line": frame.get("line", None),
                    "samples": count,
                    "pct": round(pct, 1),
                })
                profile.samples_collected += count

        profile.duration_seconds = data.get("details", {}).get("duration", 0)

    except Exception:
        pass

    return profile


def profile_function(
    fn,
    workdir: Path | None = None,
    iterations: int = 100,
) -> dict:
    """
    Profile a single function directly (no subprocess needed).

    Decorator-style: call the function N times and report timing stats.

    Usage:
        result = profile_function(lambda: my_gen_function(input), iterations=50)
        print(result)  # {total_time, avg_time, min, max, stddev}
    """
    import time
    import statistics

    times: list[float] = []

    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return {
        "iterations": iterations,
        "total_time": sum(times),
        "avg_time": statistics.mean(times),
        "min_time": min(times),
        "max_time": max(times),
        "stddev": statistics.stdev(times) if len(times) > 1 else 0,
        "throughput": iterations / sum(times) if sum(times) > 0 else float("inf"),
    }
