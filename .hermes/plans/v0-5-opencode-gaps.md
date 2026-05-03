# Forge v0.5: OpenCode Feature Parity — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> The work is divided into 5 independent tracks that can run in parallel after the first 3 foundational tasks.
> Each task is 2-5 minutes of focused work. Test-first throughout.

**Goal:** Add surgical patch editing, file reading, LSP integration, retry/backoff, context compaction, and a permission system to forge — production-quality, not toy code.

**Architecture:** New `forge/patch.py` (pure functions, no external deps), new `forge/file_service.py` (git-aware file reads), new `forge/retry.py` (exponential backoff), new `forge/compactor.py` (anchored summarization), extended `forge/runners/common.py` (patch-aware write_files), extended `forge/llm.py` (retry wrapper). LSP is opt-in via a new `forge/lsp/` subpackage.

**Tech Stack:** Python 3.11+, stdlib `difflib` for unified diffs, `git` CLI subprocess for git-aware reads, SQLite already in use, structlog for logging.

---

## Track A: Foundation (must run first, sequentially)

### Task A1: Write `forge/patch.py` — pure patch parser + surgical applier

**Objective:** Implement the custom patch format parser and surgical file editor from scratch.

**Files:**
- Create: `src/forge/patch.py`
- Create: `tests/test_patch.py`

**Step 1: Write failing test**

```python
# tests/test_patch.py
import pytest
from forge.patch import parse_patch, apply_patch, PatchHunk, UpdateFileChunk

def test_parse_add_file():
    text = """*** Begin Patch
*** Add File: src/new.py
+class Foo:
+    pass
*** End Patch"""
    result = parse_patch(text)
    assert len(result["hunks"]) == 1
    assert result["hunks"][0].type == "add"
    assert result["hunks"][0].path == "src/new.py"
    assert "class Foo" in result["hunks"][0].contents

def test_parse_update_hunks():
    text = """*** Begin Patch
*** Update File: src/foo.py
@@ old line 1
-removed line
+added line
  context line
*** End Patch"""
    result = parse_patch(text)
    hunk = result["hunks"][0]
    assert hunk.type == "update"
    assert hunk.path == "src/foo.py"
    assert len(hunk.chunks) == 1
    assert hunk.chunks[0].old_lines == ["removed line"]
    assert hunk.chunks[0].new_lines == ["added line"]

def test_parse_delete_file():
    text = """*** Begin Patch
*** Delete File: src/garbage.py
*** End Patch"""
    result = parse_patch(text)
    assert result["hunks"][0].type == "delete"
    assert result["hunks"][0].path == "src/garbage.py"

def test_surgical_edit_apply(tmp_path):
    # Create original file
    foo = tmp_path / "foo.py"
    foo.write_text("line1\nline2\nline3\nline4\nline5\n")

    text = """*** Begin Patch
*** Update File: foo.py
@@ line2
-line2
+line2 modified
*** End Patch"""
    hunks = parse_patch(text)["hunks"]
    apply_patch(hunks, tmp_path)

    lines = foo.read_text().splitlines()
    assert lines == ["line1", "line2 modified", "line3", "line4", "line5"]

def test_seek_sequence_four_pass():
    """Test that the 4-pass matching (exact, rstrip, trim, normalized) works."""
    from forge.patch import seek_sequence
    # Pass 1: exact
    assert seek_sequence(["foo  ", "bar"], ["foo  "], 0) == 0
    # Pass 2: rstrip
    assert seek_sequence(["foo  ", "bar"], ["foo"], 0) == 0
    # Pass 3: trim
    assert seek_sequence(["  foo  ", "bar"], ["foo"], 0) == 0
```

**Step 2: Run test to verify failure**

```
pytest tests/test_patch.py -v
Expected: FAIL — ModuleNotFoundError: No module named 'forge.patch'
```

**Step 3: Write minimal implementation**

Write `src/forge/patch.py` with:

1. `PatchHunk`, `UpdateFileChunk`, `HunkType` dataclasses
2. `parse_patch(patch_text)` → `{"hunks": list[PatchHunk]}`
   - Strip heredoc patterns (cat <<'EOF' ... EOF)
   - Parse `*** Begin Patch` / `*** End Patch` markers
   - Route each section: Add File / Delete File / Update File
   - Update File: parse `@@` context chunks with `+/-/ ` lines
3. `seek_sequence(lines, pattern, start_idx, eof=False)` — 4 passes:
   - Pass 1: exact string equality
   - Pass 2: `a.rstrip() == b.rstrip()`
   - Pass 3: `a.trim() == b.trim()`
   - Pass 4: normalize unicode (smart quotes → ASCII, etc.)
   - If `eof=True`, try matching from end of file first
4. `compute_replacements(original_lines, chunks) → list[(start, old_len, new_lines)]`
   - For each chunk, find old_lines in original via `seek_sequence`
   - Handle pure addition (old_lines=[] → insert at EOF or end)
   - Handle EOF anchor (`is_end_of_file` flag)
5. `apply_replacements(lines, replacements) → new_lines`
   - Sort replacements descending by index
   - `list.splice(start, old_len, *new_lines)` equivalent
6. `derive_new_contents(file_path, chunks) → (new_content, unified_diff)`
7. `apply_patch(hunks, workdir)` — filesystem side effects
   - `add`: `Path.write_text`
   - `delete`: `Path.unlink()`
   - `update`: `derive_new_contents` → `Path.write_text`
   - Handle `move_path` (write to new path, delete old)
8. `maybe_parse_apply_patch(argv)` — detect implicit patch from raw text

```python
# src/forge/patch.py — skeleton
from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import structlog

log = structlog.get_logger(__name__)

class HunkType(Enum):
    ADD = "add"
    DELETE = "delete"
    UPDATE = "update"

@dataclass
class UpdateFileChunk:
    old_lines: list[str]
    new_lines: list[str]
    change_context: str | None = None
    is_end_of_file: bool = False

@dataclass
class PatchHunk:
    type: HunkType
    path: str
    contents: str = ""          # for ADD
    chunks: list[UpdateFileChunk] = field(default_factory=list)  # for UPDATE
    move_path: str | None = None  # for UPDATE (rename)

def parse_patch(patch_text: str) -> {"hunks": list[PatchHunk]}:
    ...

def seek_sequence(lines: list[str], pattern: list[str], start_idx: int, eof=False) -> int:
    ...

def compute_replacements(original_lines: list[str], chunks: list[UpdateFileChunk]) -> list[tuple[int, int, list[str]]]:
    ...

def apply_replacements(lines: list[str], replacements: list[tuple[int, int, list[str]]]) -> list[str]:
    ...

def derive_new_contents(file_path: Path, chunks: list[UpdateFileChunk]) -> tuple[str, str]:
    ...

def apply_patch(hunks: list[PatchHunk], workdir: Path) -> dict:
    """Apply hunks to filesystem. Returns {added, modified, deleted}."""
    ...

def maybe_parse_apply_patch(argv: list[str]) -> dict | None:
    """Detect if argv looks like a patch invocation."""
    ...
```

**Step 4: Run test to verify pass**

```
pytest tests/test_patch.py -v
Expected: 6 passed
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/patch.py tests/test_patch.py
git commit -m "feat(patch): surgical patch parser and apply engine"
```

---

### Task A2: Write `forge/retry.py` — exponential backoff with rate-limit awareness

**Objective:** Add retry logic to LLM calls with proper backoff and error classification.

**Files:**
- Create: `src/forge/retry.py`
- Create: `tests/test_retry.py`

**Step 1: Write failing test**

```python
# tests/test_retry.py
import pytest, time
from forge.retry import RetryPolicy, retryable, delay, APIError

class MockAPIError:
    def __init__(self, message, status_code=None, is_retryable=None, response_headers=None):
        self.data = type("obj", (), {"message": message, "statusCode": status_code, "isRetryable": is_retryable, "responseHeaders": response_headers})()

def test_delay_exponential_backoff():
    # delay(attempt=1) should be 2000ms
    assert delay(1) == 2000
    # delay(attempt=3) should be 2000 * 2^2 = 8000
    assert delay(3) == 8000
    # delay(attempt=10) should cap at MAX_DELAY (2_147_483_647)
    assert delay(10) == 2_147_483_647

def test_delay_respects_retry_after_header_ms():
    err = MockAPIError("rate limited", response_headers={"retry-after-ms": "500"})
    assert delay(1, err) == 500

def test_delay_respects_retry_after_seconds():
    err = MockAPIError("rate limited", response_headers={"retry-after": "2"})
    assert delay(1, err) == 2000

def test_delay_respects_retry_after_http_date():
    import time
    future = time.time() + 5  # 5 seconds from now
    http_date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(future))
    err = MockAPIError("rate limited", response_headers={"retry-after": http_date})
    d = delay(1, err)
    assert 4000 < d < 6000  # approximately 5 seconds

def test_delay_caps_at_max():
    assert delay(100) == 2_147_483_647

def test_retryable_5xx_always_retryable():
    err = MockAPIError("Internal Server Error", status_code=500)
    assert retryable(err) is not None

def test_retryable_429_rate_limit():
    err = MockAPIError("rate limit exceeded", status_code=429, is_retryable=True)
    assert retryable(err) is not None

def test_retryable_context_overflow_never_retryable():
    class OverflowErr:
        data = type("obj", (), {"message": "context overflow"})()
        @staticmethod
        def is_instance(e): return "context overflow" in str(e)
    # This tests that ContextOverflowError patterns return None (not retryable)
    err = MockAPIError("context_overflow")
    assert retryable(err) is None  # No specific pattern match for this plain text

def test_retryable_overloaded_pattern():
    err = MockAPIError("Provider is Overloaded", status_code=503)
    assert retryable(err) == "Provider is Overloaded"

def test_retryable_plain_text_rate_limit():
    err = MockAPIError("Rate limit increased too quickly")
    assert retryable(err) is not None
```

**Step 2: Run test to verify failure**

```
pytest tests/test_retry.py -v
Expected: FAIL — ModuleNotFoundError: No module named 'forge.retry'
```

**Step 3: Write minimal implementation**

```python
# src/forge/retry.py
from __future__ import annotations
import time
import re
import structlog
from dataclasses import dataclass
from typing import Optional

log = structlog.get_logger(__name__)

RETRY_INITIAL_DELAY = 2000   # ms
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY = 2_147_483_647  # max 32-bit signed int
RETRY_MAX_DELAY_NO_HEADERS = 30_000

class APIError:
    """Minimal API error struct matching what the LLM backends raise."""
    def __init__(self, message: str, status_code: int = None,
                 is_retryable: bool = None, response_headers: dict = None):
        self.data = _ErrorData(message, status_code, is_retryable, response_headers)

    @staticmethod
    def is_instance(e) -> bool:
        return isinstance(e, APIError)

class _ErrorData:
    def __init__(self, message, status_code, is_retryable, response_headers):
        self.message = message
        self.statusCode = status_code
        self.isRetryable = is_retryable
        self.responseHeaders = response_headers or {}

def cap(ms: float) -> float:
    return min(ms, RETRY_MAX_DELAY)

def delay(attempt: int, error: Optional[APIError] = None) -> float:
    """Compute delay in ms for a given attempt. Respects Retry-After headers."""
    if error and error.data.responseHeaders:
        headers = error.data.responseHeaders
        # retry-after-ms takes priority
        ms = headers.get("retry-after-ms")
        if ms:
            try:
                return cap(float(ms))
            except ValueError:
                pass
        # retry-after as seconds
        ra = headers.get("retry-after")
        if ra:
            try:
                return cap(float(ra) * 1000)
            except ValueError:
                pass
            # HTTP date format
            try:
                parsed = time.strptime(ra, "%a, %d %b %Y %H:%M:%S %Z")
                delta_ms = (time.mktime(parsed) - time.time()) * 1000
                if delta_ms > 0:
                    return cap(delta_ms)
            except ValueError:
                pass
        # Exponential backoff as fallback
        return cap(RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)))
    return cap(min(
        RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1)),
        RETRY_MAX_DELAY_NO_HEADERS
    ))

def retryable(error: APIError) -> Optional[str]:
    """Classify an error. Return None if not retryable, or a reason string."""
    if not isinstance(error, APIError):
        return None
    d = error.data
    # Context overflow — never retry
    if "context_overflow" in d.message.lower() or "context overflow" in d.message.lower():
        return None
    # 5xx always retry
    if d.statusCode and d.statusCode >= 500:
        return d.message or "Server error"
    # Explicit retryable flag
    if d.isRetryable is False:
        return None
    # Overloaded pattern
    if "overloaded" in d.message.lower():
        return d.message
    # Rate limit patterns in plain text
    lower = d.message.lower()
    if any(p in lower for p in ("rate increased too quickly", "rate limit", "too many requests")):
        return d.message
    return None

@dataclass
class RetryPolicy:
    """Configurable retry policy for LLM calls."""
    max_attempts: int = 5
    initial_delay_ms: float = RETRY_INITIAL_DELAY
    backoff_factor: float = RETRY_BACKOFF_FACTOR
    max_delay_ms: float = RETRY_MAX_DELAY

    def compute_delay(self, attempt: int, error: Optional[APIError] = None) -> float:
        return delay(attempt, error)

    def is_retryable(self, error: APIError) -> bool:
        return retryable(error) is not None

    def should_retry(self, attempt: int, error: APIError) -> bool:
        if attempt >= self.max_attempts:
            return False
        return self.is_retryable(error)
```

**Step 4: Run test to verify pass**

```
pytest tests/test_retry.py -v
Expected: 10 passed
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/retry.py tests/test_retry.py
git commit -m "feat(retry): exponential backoff with Retry-After header support"
```

---

### Task A3: Write `forge/file_service.py` — git-aware file reading with structured content

**Objective:** Give forge the ability to read files with git-aware diff computation.

**Files:**
- Create: `src/forge/file_service.py`
- Create: `tests/test_file_service.py`

**Step 1: Write failing test**

```python
# tests/test_file_service.py
import pytest, subprocess
from pathlib import Path
from forge.file_service import FileService, Content

@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    return tmp_path

def test_read_text_file(git_repo):
    (git_repo / "hello.txt").write_text("Hello, world!\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=git_repo, check=True)

    svc = FileService(git_repo)
    content = svc.read("hello.txt")
    assert content.type == "text"
    assert content.content == "Hello, world!"

def test_read_nonexistent_file_returns_empty_text(git_repo):
    svc = FileService(git_repo)
    content = svc.read("does_not_exist.py")
    assert content.type == "text"
    assert content.content == ""

def test_status_git_changes(git_repo):
    (git_repo / "foo.txt").write_text("foo\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True)

    # Modify a file
    (git_repo / "foo.txt").write_text("foo modified\n")

    svc = FileService(git_repo)
    status = svc.status()
    modified = [f for f in status if f.status == "modified"]
    assert len(modified) == 1
    assert "foo.txt" in modified[0].path

def test_search_files(git_repo):
    (git_repo / "alpha.py").write_text("alpha")
    (git_repo / "beta.py").write_text("beta")
    (git_repo / "gamma.py").write_text("gamma")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True)

    svc = FileService(git_repo)
    results = svc.search("alpha")
    assert "alpha.py" in results

def test_list_directory(git_repo):
    (git_repo / "src").mkdir()
    (git_repo / "src" / "main.py").write_text("main")
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_main.py").write_text("test")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True)

    svc = FileService(git_repo)
    nodes = svc.list()
    names = [n.name for n in nodes]
    assert "src" in names
    assert "tests" in names
```

**Step 2: Run test to verify failure**

```
pytest tests/test_file_service.py -v
Expected: FAIL — ModuleNotFoundError: No module named 'forge.file_service'
```

**Step 3: Write minimal implementation**

```python
# src/forge/file_service.py
"""Git-aware file reading, searching, and status for forge.

FileService wraps git + filesystem operations with:
- read(): text content with git-aware diff (structuredPatch from git show/diff)
- status(): git diff --numstat for modified/added/deleted
- search(): fuzzy file search using file index
- list(): directory listing with .gitignore filtering
"""

from __future__ import annotations
import subprocess
import structlog
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


class FileType(Enum):
    TEXT = "text"
    BINARY = "binary"


@dataclass
class FileInfo:
    path: str
    type: FileType
    content: str = ""
    diff: str = ""          # unified diff against HEAD
    encoding: Optional[str] = None  # "base64" if binary/text-but-encoded


class GitNotAvailable(ValueError):
    """Raised when the directory is not a git repository."""
    pass


class FileService:
    """Git-aware file operations for the executor."""

    # Binary file extensions
    _binary_extensions = {
        "exe", "dll", "pdb", "bin", "so", "dylib", "o", "a", "lib",
        "wav", "mp3", "ogg", "flac", "aac", "mp4", "avi", "mov",
        "zip", "tar", "gz", "bz2", "7z", "rar", "pdf", "doc",
        "docx", "ppt", "pptx", "xls", "xlsx", "sqlite", "db",
        "png", "jpg", "jpeg", "gif", "bmp", "webp", "ico", "svg",
    }

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._verify_git()

    def _verify_git(self):
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(self.workdir),
            capture_output=True, text=True,
        )
        if result.stdout.strip() != "true":
            raise GitNotAvailable(f"{self.workdir} is not a git repository")

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        cfg = [
            "-c", "core.autocrlf=false",
            "-c", "core.fsmonitor=false",
            "-c", "core.quotepath=false",
        ]
        return subprocess.run(
            ["git"] + cfg + args,
            cwd=str(self.workdir),
            capture_output=True, text=True,
            check=check,
        )

    def _is_binary(self, path: Path) -> bool:
        return path.suffix.lstrip(".") in self._binary_extensions

    def read(self, file: str) -> FileInfo:
        """Read a file, returning text content and git diff against HEAD."""
        full = (self.workdir / file) if not Path(file).is_absolute() else Path(file)
        rel = full.relative_to(self.workdir) if full.is_relative_to(self.workdir) else full

        if not full.exists():
            return FileInfo(path=str(rel), type=FileType.TEXT, content="")

        if self._is_binary(full):
            try:
                import base64
                data = full.read_bytes()
                return FileInfo(
                    path=str(rel), type=FileType.BINARY, encoding="base64",
                    content=base64.b64encode(data).decode("ascii"),
                )
            except Exception:
                return FileInfo(path=str(rel), type=FileType.BINARY, content="")

        try:
            content = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            import base64
            return FileInfo(
                path=str(rel), type=FileType.BINARY, encoding="base64",
                content=base64.b64encode(full.read_bytes()).decode("ascii"),
            )

        # Compute git diff against HEAD
        diff = ""
        try:
            diff_result = self._run_git(["diff", "--", str(rel)], check=False)
            diff = diff_result.stdout
            if not diff.strip():
                staged = self._run_git(["diff", "--staged", "--", str(rel)], check=False)
                diff = staged.stdout
        except Exception:
            pass

        return FileInfo(path=str(rel), type=FileType.TEXT, content=content, diff=diff)

    def status(self) -> list[FileInfo]:
        """Return all files that differ from HEAD."""
        result = self._run_git(["diff", "--numstat", "HEAD"], check=False)
        changed = []

        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                added_str, removed_str, path = parts
                added = int(added_str) if added_str != "-" else 0
                removed = int(removed_str) if removed_str != "-" else 0
                file_type = FileType.TEXT
                try:
                    if self._is_binary(Path(path)):
                        file_type = FileType.BINARY
                except Exception:
                    pass
                changed.append(FileInfo(
                    path=path, type=file_type,
                    content=f"+{added}/-{removed}",  # summary in content field
                ))

        # Untracked files
        untracked = self._run_git(["ls-files", "--others", "--exclude-standard"], check=False)
        if untracked.stdout.strip():
            for path in untracked.stdout.strip().split("\n"):
                if path:
                    changed.append(FileInfo(path=path, type=FileType.TEXT))

        # Deleted files
        deleted = self._run_git(["diff", "--name-only", "--diff-filter=D", "HEAD"], check=False)
        if deleted.stdout.strip():
            for path in deleted.stdout.strip().split("\n"):
                if path:
                    changed.append(FileInfo(path=path, type=FileType.TEXT))

        return changed

    def search(self, query: str, limit: int = 100) -> list[str]:
        """Fuzzy search for files by name using simple substring + scoring."""
        import subprocess
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.workdir), capture_output=True, text=True, check=True,
            )
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        except subprocess.CalledProcessError:
            all_files = []
            for p in self.workdir.rglob("*"):
                if p.is_file() and not any(
                    part.startswith(".") for part in p.parts
                ):
                    all_files.append(str(p.relative_to(self.workdir)))

        query_lower = query.lower()
        scored = []
        for f in all_files:
            name = Path(f).name.lower()
            if query_lower in name:
                # Better matches first
                score = 0 if name.startswith(query_lower) else 1
                scored.append((score, f))
        scored.sort(key=lambda x: x[0])
        return [f for _, f in scored[:limit]]

    def list(self, dir: str = ".") -> list[FileInfo]:
        """List files and directories at a given path, respecting .gitignore."""
        import ignore
        full = self.workdir / dir
        if not full.exists():
            return []

        ig = ignore.Ignore()
        try:
            gi = (full / ".gitignore")
            if gi.exists():
                ig.add(gi.read_text())
        except Exception:
            pass

        results = []
        for entry in full.iterdir():
            rel = entry.relative_to(self.workdir)
            is_dir = entry.is_dir()
            if ig.ignores(str(rel) + ("/" if is_dir else "")):
                continue
            results.append(FileInfo(
                path=str(rel),
                type=FileType.TEXT,  # dirs don't have a type
            ))
        return sorted(results, key=lambda x: (x.type.value, x.path))
```

**Step 4: Run test to verify pass**

```
pytest tests/test_file_service.py -v
Expected: 5 passed
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/file_service.py tests/test_file_service.py
git commit -m "feat(file): git-aware FileService with read/status/search/list"
```

---

## Track B: Patch-Aware File Writing (depends on A1)

### Task B1: Extend `write_files` in `common.py` to support surgical patch actions

**Objective:** Replace the naive full-file `write_text` with surgical patch application when `action: "patch"` is present.

**Files:**
- Modify: `src/forge/runners/common.py` (add `apply_patch_files`, update `write_files`)
- Modify: `tests/test_common.py` (add tests for patch action)

**Step 1: Write failing test**

```python
# Add to tests/test_common.py
def test_write_files_patch_action(tmp_path):
    """Test that action=patch applies surgical edits instead of full overwrite."""
    from forge.patch import parse_patch
    from forge.runners.common import apply_patch_files

    # Create original file
    foo = tmp_path / "foo.py"
    foo.write_text("line1\nline2\nline3\nline4\nline5\n")

    # Surgical patch
    patch_text = """*** Begin Patch
*** Update File: foo.py
@@ line2
-line2
+line2 modified
*** End Patch"""

    hunks = parse_patch(patch_text)["hunks"]
    apply_patch_files(hunks, tmp_path)

    lines = foo.read_text().splitlines()
    assert lines == ["line1", "line2 modified", "line3", "line4", "line5"]
```

**Step 2: Run test to verify failure**

```
pytest tests/test_common.py::test_write_files_patch_action -v
Expected: FAIL — apply_patch_files not in common.py
```

**Step 3: Add to common.py**

```python
# In common.py, add:

def apply_patch_files(hunks: list, workdir: Path) -> list[str]:
    """Apply patch hunks to filesystem. Returns list of affected paths."""
    from forge.patch import apply_patch
    return apply_patch(hunks, workdir)
```

**Step 4: Run test to verify pass**

```
pytest tests/test_common.py::test_write_files_patch_action -v
Expected: PASS
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/runners/common.py tests/test_common.py
git commit -m "feat(common): patch action support in write_files"
```

---

### Task B2: Update EXECUTOR_SYSTEM prompt to emit patch actions

**Objective:** Update the executor prompt so the LLM knows to use `action: "patch"` for surgical edits instead of always full-file replacement.

**Files:**
- Modify: `src/forge/runners/common.py` (EXECUTOR_SYSTEM string)

**Step 1: Read current EXECUTOR_SYSTEM**

```python
# From common.py — current prompt:
EXECUTOR_SYSTEM = """You are the Executor in a directed-graph agentic coding system (forge).
...
For each file output a JSON entry: {"path": "...", "action": "create"|"update"|"delete", "content": "..."}.
Only include files that need to be created/updated.
"""
```

**Step 2: Update prompt**

Replace with an expanded prompt that includes patch format documentation and the FileService context:

```python
EXECUTOR_SYSTEM = """You are the Executor in a directed-graph agentic coding system (forge).
You have a SPEC.md. Generate all the code files described.

IMPORTANT — TDD-FIRST WORKFLOW:
1. Write tests BEFORE implementation code
2. Then write implementation to make tests pass
3. Verify all tests pass

IMPORTANT — SURGICAL EDITS:
For EXISTING files you want to change, use action "patch" with a surgical patch format.
NEVER use action "update" on existing files — it causes unnecessary diff noise.
Only use "create" for genuinely new files.

PATCH FORMAT for existing files (action: "patch"):
Output a JSON entry: {"path": "...", "action": "patch", "patch": "..."}
The patch text uses this format:
*** Begin Patch
*** Update File: path/to/file.py
@@ context line that uniquely identifies location
-old line to remove
+new line to add
  context line (unchanged, for anchoring)
*** End Patch

Rules for patches:
- Each chunk starts with @@ followed by a context line
- Lines starting with - are removed from the original
- Lines starting with + are added to the original
- Unchanged context lines (prefixed with space) anchor the match
- Use multiple chunks for discontiguous changes
- For additions at end of file, use @@ as the last line of the file
- For deletions, omit the + line entirely

For new files, use: {"path": "...", "action": "create", "content": "..."}
For deletions, use: {"path": "...", "action": "delete"}
"""
```

**Step 3: No test needed — this is a prompt string change**

**Step 4: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/runners/common.py
git commit -m "feat(executor): add surgical patch format to EXECUTOR_SYSTEM prompt"
```

---

## Track C: LLM Retry Wrapper (depends on A2)

### Task C1: Wire retry into `forge/llm.py` LLM backend

**Objective:** Wrap every LLM `complete()` call with retry + exponential backoff.

**Files:**
- Modify: `src/forge/llm.py`

**Step 1: Read llm.py to understand the backend interface**

```
cat /home/shaheen/forge/src/forge/llm.py
```

**Step 2: Add retry wrapper to `complete()` method on each backend**

For each backend class (MMXBackend, OpenAIBackend, etc.), wrap the HTTP request in:

```python
def complete(self, prompt, system=None, max_tokens=1024, temperature=0.2, **kwargs):
    from forge.retry import RetryPolicy, APIError, retryable, delay
    policy = RetryPolicy(max_attempts=5)
    attempt = 0
    last_error = None
    while True:
        attempt += 1
        try:
            return self._complete(prompt, system, max_tokens, temperature, **kwargs)
        except Exception as e:
            last_error = e
            api_err = APIError(str(e))
            reason = retryable(api_err)
            if not reason or not policy.should_retry(attempt, api_err):
                raise
            log.warning("llm.retry", attempt=attempt, reason=reason)
            time.sleep(delay(attempt, api_err) / 1000)
```

**Step 3: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/llm.py
git commit -m "feat(llm): wrap complete() with RetryPolicy and exponential backoff"
```

---

## Track D: Context Compaction (independent after A1-A3)

### Task D1: Write `forge/compactor.py` — anchored context summarization

**Objective:** Implement anchored summarization: newer turns kept verbatim, older context summarized.

**Files:**
- Create: `src/forge/compactor.py`
- Create: `tests/test_compactor.py`

**Step 1: Write failing test**

```python
# tests/test_compactor.py
import pytest
from forge.compactor import Compactor, AnchorConfig

def test_preserve_newer_turns():
    """Newer conversation turns (above anchor) are kept verbatim."""
    compactor = Compactor(max_tokens=500)

    # Simulate a conversation with older and newer turns
    older = [{"role": "user", "content": "Fix the bug in parser.py" * 50}]
    newer = [{"role": "user", "content": "Now add tests for it"}]
    anchor = AnchorConfig(keep_newer_turns=1)

    result = compactor.compact(older + newer, anchor=anchor)
    # The newer turn should be preserved verbatim
    assert any("add tests" in str(t) for t in result["kept"])
    # Older content should be summarized
    assert "bug" in result["summary"].lower()

def test_summarize_old_content():
    """Content older than anchor gets summarized, not discarded."""
    compactor = Compactor(max_tokens=200)
    old_turns = [
        {"role": "user", "content": "Build a REST API with Flask and SQLAlchemy. Include auth, rate limiting, and OpenAPI docs. Deploy with Docker."},
        {"role": "assistant", "content": "Created SPEC.md, implemented auth, rate limiting, OpenAPI. Dockerfile added."},
    ]
    anchor = AnchorConfig(keep_newer_turns=0)
    result = compactor.compact(old_turns, anchor=anchor)
    # Summary should exist and be shorter than original
    assert len(result["summary"]) < sum(len(t["content"]) for t in old_turns)
    assert result["summary"] != ""

def test_empty_compact():
    """Compacting an empty or small session returns empty summary."""
    compactor = Compactor(max_tokens=500)
    result = compactor.compact([], anchor=AnchorConfig(keep_newer_turns=0))
    assert result["summary"] == ""
```

**Step 2: Run test to verify failure**

```
pytest tests/test_compactor.py -v
Expected: FAIL — ModuleNotFoundError
```

**Step 3: Write implementation**

```python
# src/forge/compactor.py
"""Anchored context summarization for forge sessions.

Strategy:
- Keep the N most recent turns verbatim (anchor zone)
- Summarize everything below the anchor into a dense paragraph
- Preserve exact file paths, function names, technical terms in summary
- Output: {"kept": [...turns above anchor], "summary": "..."}
"""

from __future__ import annotations
import structlog
from dataclasses import dataclass

log = structlog.get_logger(__name__)


@dataclass
class AnchorConfig:
    """Configuration for anchored summarization."""
    keep_newer_turns: int = 3  # How many newest turns to preserve verbatim
    summary_style: str = "pr_description"  # "pr_description" | "detailed"


class Compactor:
    """Anchored context summarizer — newer turns verbatim, older summarized."""

    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens

    def compact(self, turns: list[dict], anchor: AnchorConfig = None) -> dict:
        """
        Compact a list of conversation turns.

        Returns:
            {
                "kept": [...turns in the anchor zone, verbatim],
                "summary": "Summarized version of older turns"
            }
        """
        if anchor is None:
            anchor = AnchorConfig()
        if not turns:
            return {"kept": [], "summary": ""}

        # Split: anchor zone (newest) vs history zone (older)
        k = anchor.keep_newer_turns
        history = turns[:-k] if k < len(turns) else turns[:0]
        anchor_zone = turns[-k:] if k > 0 else []

        if not history:
            return {"kept": anchor_zone, "summary": ""}

        summary = self._summarize(history, anchor.summary_style)
        return {"kept": anchor_zone, "summary": summary}

    def _summarize(self, turns: list[dict], style: str) -> str:
        """
        Summarize older turns into a dense paragraph.
        Uses the LLM if available, otherwise falls back to simple extraction.
        """
        if not turns:
            return ""

        # Extract key facts: file paths, decisions, changes made
        facts = self._extract_facts(turns)

        if style == "pr_description":
            return self._pr_description(facts, turns)
        return self._detailed_summary(facts, turns)

    def _extract_facts(self, turns: list[dict]) -> dict:
        """Extract structured facts from turns: files touched, decisions, errors."""
        facts = {
            "files": set(),
            "decisions": [],
            "errors": [],
            "features": [],
        }
        for turn in turns:
            content = turn.get("content", "")
            # Extract file paths (simple heuristic)
            import re
            files = re.findall(r'[\w./\\]+\.py|\.ts|\.js|\.md|\.yaml|\.json', content)
            facts["files"].update(files)
        return facts

    def _pr_description(self, facts: dict, turns: list[dict]) -> str:
        """Format summary as PR description: 2-3 sentences, first person."""
        files = list(facts["files"])
        if not files:
            files_str = "the codebase"
        elif len(files) <= 3:
            files_str = ", ".join(f"`{f}`" for f in files)
        else:
            files_str = f"{', '.join('`'+f+'`' for f in files[:3])} and {len(files)-3} more files"

        decisions = " ".join(facts["decisions"]) if facts["decisions"] else None
        summary = f"Worked on {files_str}."
        if decisions:
            summary += f" {decisions}."
        # Cap at reasonable length
        if len(summary) > 500:
            summary = summary[:497] + "..."
        return summary

    def _detailed_summary(self, facts: dict, turns: list[dict]) -> str:
        """More detailed paragraph summarization."""
        lines = ["Previous session context:"]
        files = list(facts["files"])
        if files:
            lines.append(f"- Files involved: {', '.join(files[:10])}")
        for decision in facts["decisions"]:
            lines.append(f"- Decision: {decision}")
        return "\n".join(lines) if len(lines) > 1 else ""
```

**Step 4: Run test to verify pass**

```
pytest tests/test_compactor.py -v
Expected: 3 passed
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/compactor.py tests/test_compactor.py
git commit -m "feat(compactor): anchored context summarization"
```

---

## Track E: Permission System (independent after A1-A3)

### Task E1: Write `forge/permissions.py` — per-agent permission ruleset

**Objective:** Implement permission ruleset (build=full, plan=read-only, .env=ask).

**Files:**
- Create: `src/forge/permissions.py`
- Create: `tests/test_permissions.py`

**Step 1: Write failing test**

```python
# tests/test_permissions.py
import pytest
from forge.permissions import PermissionSet, PermissionResult

def test_build_agent_has_all_permissions():
    ps = PermissionSet.for_agent("build")
    assert ps.allows("read", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("write", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("delete", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("patch", "src/foo.py") == PermissionResult.ALLOW

def test_plan_agent_denies_all_edits():
    ps = PermissionSet.for_agent("plan")
    assert ps.allows("read", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("write", "src/foo.py") == PermissionResult.DENY
    assert ps.allows("patch", "src/foo.py") == PermissionResult.DENY
    assert ps.allows("delete", "src/foo.py") == PermissionResult.DENY

def test_plan_allows_plan_files():
    ps = PermissionSet.for_agent("plan")
    # Plan files are allowed in plan mode
    assert ps.allows("write", ".opencode/plans/feature.md") == PermissionResult.ALLOW
    assert ps.allows("write", "plans/feature.md") == PermissionResult.ALLOW

def test_env_files_require_ask():
    ps = PermissionSet.for_agent("build")
    assert ps.allows("read", ".env") == PermissionResult.ASK
    assert ps.allows("read", ".env.local") == PermissionResult.ASK
    assert ps.allows("read", ".env.example") == PermissionResult.ALLOW  # safe variant

def test_doom_loop_requires_ask():
    ps = PermissionSet.for_agent("build")
    assert ps.allows("doom_loop", "") == PermissionResult.ASK

def test_external_directory_requires_ask():
    ps = PermissionSet.for_agent("build")
    assert ps.allows("external_directory", "/etc/passwd") == PermissionResult.DENY
    # But allowed dirs are permitted
    assert ps.allows("external_directory", "/tmp/forge-workspace/") == PermissionResult.ALLOW
```

**Step 2: Run test to verify failure**

```
pytest tests/test_permissions.py -v
Expected: FAIL — ModuleNotFoundError
```

**Step 3: Write implementation**

```python
# src/forge/permissions.py
"""Per-agent permission ruleset for forge.

Agents:
  build   — full access, normal operations
  plan    — read-only, plan file writes allowed
  review  — read + patch, no create/delete

Special rules:
  .env files        → ASK (user must confirm)
  doom_loop         → ASK (prevents infinite loops)
  external_directory → scoped whitelist
"""

from __future__ import annotations
import os
import structlog
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


class PermissionResult(Enum):
    ALLOW = "allow"      # permitted without question
    DENY = "deny"        # immediately blocked
    ASK = "ask"          # require user confirmation


# Patterns for .env file detection
_ENV_PATTERNS = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
)
_SAFE_ENV = (".env.example", ".env.template", ".env.sample")


@dataclass
class PermissionRule:
    result: PermissionResult
    pattern: Optional[str] = None  # glob-like pattern for path matching


class PermissionSet:
    """
    A ruleset for a specific agent.
    Evaluates permission requests against ordered rules.
    """

    def __init__(self, rules: list[PermissionRule], allow_by_default: bool = True):
        self.rules = rules
        self.allow_by_default = allow_by_default

    def allows(self, action: str, path: str = "") -> PermissionResult:
        """Check if an action on a path is allowed."""
        for rule in self.rules:
            if self._matches(action, path, rule):
                return rule.result
        return PermissionResult.ALLOW if self.allow_by_default else PermissionResult.DENY

    def _matches(self, action: str, path: str, rule: PermissionRule) -> bool:
        """Check if an action/path matches a rule."""
        # Simple action match (no pattern)
        if rule.pattern is None:
            return True
        # Glob-style path matching
        import fnmatch
        return fnmatch.fnmatch(path, rule.pattern)

    @staticmethod
    def for_agent(agent: str) -> "PermissionSet":
        """Get the default permission set for an agent type."""
        base_rules: list[PermissionRule] = [
            # doom_loop always requires confirmation
            PermissionRule(PermissionResult.ASK, pattern="doom_loop"),
            # External directory access is denied by default
            PermissionRule(PermissionResult.DENY, pattern="external_directory:*"),
        ]

        if agent == "build":
            return PermissionSet([
                # Read is allowed for all text files
                PermissionRule(PermissionResult.ALLOW, pattern="read:*"),
                # .env files require confirmation
                PermissionRule(PermissionResult.ASK, pattern="read:.env*"),
                # Writes allowed
                PermissionRule(PermissionResult.ALLOW, pattern="write:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="patch:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="create:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="delete:*"),
                # External directory whitelist
                PermissionRule(
                    PermissionResult.ALLOW,
                    pattern="external_directory:/tmp/forge*",
                ),
                PermissionRule(
                    PermissionResult.ALLOW,
                    pattern="external_directory:~/.forge*",
                ),
            ] + base_rules)

        elif agent == "plan":
            return PermissionSet([
                # Read-only
                PermissionRule(PermissionResult.ALLOW, pattern="read:*"),
                # Plan files in .opencode/plans/ are allowed
                PermissionRule(PermissionResult.ALLOW, pattern="write:.opencode/plans/*"),
                PermissionRule(PermissionResult.ALLOW, pattern="write:**/plans/*.md"),
                # Everything else is denied
                PermissionRule(PermissionResult.DENY, pattern="write:*"),
                PermissionRule(PermissionResult.DENY, pattern="patch:*"),
                PermissionRule(PermissionResult.DENY, pattern="create:*"),
                PermissionRule(PermissionResult.DENY, pattern="delete:*"),
            ] + base_rules)

        elif agent == "review":
            return PermissionSet([
                PermissionRule(PermissionResult.ALLOW, pattern="read:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="patch:*"),
                PermissionRule(PermissionResult.DENY, pattern="write:*"),
                PermissionRule(PermissionResult.DENY, pattern="create:*"),
                PermissionRule(PermissionResult.DENY, pattern="delete:*"),
            ] + base_rules)

        else:
            # Unknown agent — read-only by default
            return PermissionSet([
                PermissionRule(PermissionResult.ALLOW, pattern="read:*"),
                PermissionRule(PermissionResult.DENY, pattern="write:*"),
                PermissionRule(PermissionResult.DENY, pattern="patch:*"),
                PermissionRule(PermissionResult.DENY, pattern="create:*"),
                PermissionRule(PermissionResult.DENY, pattern="delete:*"),
            ] + base_rules)


def check_permission(
    agent: str,
    action: str,
    path: str = "",
) -> PermissionResult:
    """Convenience function to check a permission for an agent."""
    return PermissionSet.for_agent(agent).allows(action, path)
```

**Step 4: Run test to verify pass**

```
pytest tests/test_permissions.py -v
Expected: 6 passed
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/permissions.py tests/test_permissions.py
git commit -m "feat(permissions): per-agent permission ruleset"
```

---

## Track F: LSP Integration (independent after A1-A3 — most complex, do last)

### Task F1: Write `forge/lsp/` — minimal LSP client (hover, definition, references)

**Objective:** Implement a minimal LSP integration for pyright (Python) and tsserver (TypeScript/JS).

**Files:**
- Create: `src/forge/lsp/__init__.py`
- Create: `src/forge/lsp/client.py` — LSP protocol client
- Create: `src/forge/lsp/server.py` — LSP server spawning
- Create: `src/forge/lsp/service.py` — FileService-style interface
- Create: `tests/test_lsp.py`

**Step 1: Write failing test**

```python
# tests/test_lsp.py
import pytest
from pathlib import Path
from forge.lsp.service import LSPService

@pytest.fixture
def python_project(tmp_path):
    (tmp_path / "main.py").write_text('''
def greet(name: str) -> str:
    return f"Hello, {name}"

def farewell(name: str) -> str:
    return f"Goodbye, {name}"
''')
    return tmp_path

def test_lsp_service_initialization(python_project):
    svc = LSPService(python_project)
    assert svc.workdir == python_project
    assert svc.capabilities == {}

def test_lsp_detect_language_python(python_project):
    svc = LSPService(python_project)
    assert svc._detect_language("main.py") == "python"
    assert svc._detect_language("foo.py") == "python"
    assert svc._detect_language("bar.ts") == "typescript"
    assert svc._detect_language("baz.js") == "javascript"
```

**Step 2: Run test to verify failure**

```
pytest tests/test_lsp.py -v
Expected: FAIL — ModuleNotFoundError
```

**Step 3: Write minimal implementation**

```python
# src/forge/lsp/__init__.py
"""forge.lsp — Language Server Protocol integration."""

from forge.lsp.service import LSPService

__all__ = ["LSPService"]
```

```python
# src/forge/lsp/client.py
"""LSP JSON-RPC client over stdio or TCP."""

from __future__ import annotations
import json
import structlog
import subprocess
from pathlib import Path
from typing import Any, Optional

log = structlog.get_logger(__name__)


class LSPError(Exception):
    """LSP-specific errors."""
    pass


class LSPClient:
    """
    Minimal LSP client using stdio transport.
    Communicates via JSON-RPC 2.0 over subprocess stdin/stdout.
    """

    def __init__(self, server_command: list[str], workdir: Path):
        self.workdir = workdir
        self._proc = subprocess.Popen(
            server_command,
            cwd=str(workdir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        self._request_id = 0
        self._pending: dict[int, Any] = {}
        self._initialize()

    def _initialize(self):
        """Send LSP initialize request."""
        result = self.send_request("initialize", {
            "processId": None,
            "rootUri": self.workdir.as_uri(),
            "capabilities": {},
        })
        self.send_notification("initialized", {})

    def send_request(self, method: str, params: dict) -> Any:
        """Send a JSON-RPC request and wait for response."""
        req_id = self._request_id
        self._request_id += 1
        message = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        self._proc.stdin.write(message.encode() + b"\n")
        self._proc.stdin.flush()
        # Read response
        line = self._proc.stdout.readline()
        if not line:
            raise LSPError(f"No response for {method}")
        response = json.loads(line.decode())
        if "error" in response:
            raise LSPError(f"LSP error: {response['error']}")
        return response.get("result")

    def send_notification(self, method: str, params: dict):
        """Fire-and-forget notification (no response)."""
        message = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(message.encode() + b"\n")
        self._proc.stdin.flush()

    def shutdown(self):
        """Send shutdown and terminate."""
        try:
            self.send_request("shutdown", {})
        except Exception:
            pass
        self._proc.terminate()

    # --- Convenience methods ---

    def hover(self, file: str, line: int, character: int) -> Optional[dict]:
        """Get hover info at a position."""
        try:
            return self.send_request("textDocument/hover", {
                "textDocument": {"uri": Path(file).as_uri()},
                "position": {"line": line, "character": character},
            })
        except LSPError:
            return None

    def definition(self, file: str, line: int, character: int) -> list[dict]:
        """Go to definition."""
        try:
            result = self.send_request("textDocument/definition", {
                "textDocument": {"uri": Path(file).as_uri()},
                "position": {"line": line, "character": character},
            })
            return result if isinstance(result, list) else [result] if result else []
        except LSPError:
            return []

    def references(self, file: str, line: int, character: int) -> list[dict]:
        """Find all references."""
        try:
            result = self.send_request("textDocument/references", {
                "textDocument": {"uri": Path(file).as_uri()},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            })
            return result if isinstance(result, list) else []
        except LSPError:
            return []
```

```python
# src/forge/lsp/server.py
"""LSP server spawning for pyright and tsserver."""

from __future__ import annotations
import shutil
import structlog
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


class LSPServer:
    """Describes how to spawn a specific LSP server."""

    def __init__(self, id: str, command: list[str], extensions: list[str]):
        self.id = id
        self.command = command
        self.extensions = extensions  # file extensions this server handles

    def available(self) -> bool:
        """Check if the server binary is installed."""
        if not self.command:
            return False
        binary = Path(self.command[0]).name
        return shutil.which(binary) is not None


# Known LSP servers
PYRIGHT = LSPServer(
    id="pyright",
    command=["pyright", "--stdio"],
    extensions=[".py"],
)

TSSERVER = LSPServer(
    id="tsserver",
    command=["typescript-language-server", "--stdio"],
    extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs"],
)

JEDI = LSPServer(
    id="jedi",
    command=["jedi-language-server"],
    extensions=[".py"],
)


def detect_servers(workdir: Path) -> list[LSPServer]:
    """Detect which LSP servers are available and applicable for the project."""
    # Scan for package.json (tsserver) or pyrightconfig.json / .py files (pyright)
    servers = []

    has_ts = any(workdir.rglob(f"*.{ext}")) for ext in [".ts", ".tsx", ".js", ".jsx"]))
    if has_ts and TSSERVER.available():
        servers.append(TSSERVER)

    has_py = list(workdir.rglob("*.py"))
    if has_py:
        if PYRIGHT.available():
            servers.append(PYRIGHT)
        elif JEDI.available():
            servers.append(JEDI)

    return servers
```

```python
# src/forge/lsp/service.py
"""LSP service — unified interface to language servers for a project."""

from __future__ import annotations
import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from forge.lsp.client import LSPClient
from forge.lsp.server import LSPServer, detect_servers

log = structlog.get_logger(__name__)


@dataclass
class LSPDiagnostic:
    file: str
    line: int
    column: int
    severity: str  # "error" | "warning" | "information"
    message: str
    source: str  # e.g. "pyright"


@dataclass
class LSPLocation:
    file: str
    line: int
    column: int
    end_line: Optional[int] = None
    end_column: Optional[int] = None


class LSPService:
    """
    Unified LSP interface for forge.
    Spawns appropriate server(s) based on project language.
    """

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._servers = detect_servers(workdir)
        self._clients: dict[str, LSPClient] = {}

    @property
    def capabilities(self) -> dict:
        """Return capabilities from all servers."""
        # Lazy — connect on demand
        return {}

    def _client_for_file(self, file: str) -> Optional[LSPClient]:
        """Get or spawn the appropriate LSP client for a file."""
        ext = Path(file).suffix
        for server in self._servers:
            if ext in server.extensions:
                if server.id not in self._clients:
                    try:
                        self._clients[server.id] = LSPClient(server.command, self.workdir)
                    except Exception as e:
                        log.warning("lsp.spawn_failed", server=server.id, error=str(e))
                        return None
                return self._clients[server.id]
        return None

    def hover(self, file: str, line: int, character: int) -> Optional[str]:
        """Get hover documentation for a position."""
        client = self._client_for_file(file)
        if not client:
            return None
        result = client.hover(file, line, character)
        if result and "contents" in result:
            return result["contents"].get("value", str(result["contents"]))
        return None

    def definition(self, file: str, line: int, character: int) -> list[LSPLocation]:
        """Find definition(s) of a symbol."""
        client = self._client_for_file(file)
        if not client:
            return []
        results = client.definition(file, line, character)
        return [self._parse_location(r) for r in results]

    def references(self, file: str, line: int, character: int) -> list[LSPLocation]:
        """Find all references to a symbol."""
        client = self._client_for_file(file)
        if not client:
            return []
        results = client.references(file, line, character)
        return [self._parse_location(r) for r in results]

    def diagnostics(self, file: str) -> list[LSPDiagnostic]:
        """Get current diagnostics for a file (requires server to support pull)."""
        # Publish diagnostics comes as notifications; for now return empty
        return []

    def _parse_location(self, loc: dict) -> LSPLocation:
        """Parse an LSP Location object into our format."""
        uri = loc.get("uri", "")
        try:
            path = str(Path(uri).path)
        except Exception:
            path = uri
        pos = loc.get("range", {}).get("start", {})
        end = loc.get("range", {}).get("end", {})
        return LSPLocation(
            file=path,
            line=pos.get("line", 0),
            column=pos.get("character", 0),
            end_line=end.get("line"),
            end_column=end.get("character"),
        )

    def shutdown(self):
        """Stop all LSP clients."""
        for client in self._clients.values():
            try:
                client.shutdown()
            except Exception:
                pass
```

**Step 4: Run test to verify pass**

```
pytest tests/test_lsp.py -v
Expected: 2 passed
```

**Step 5: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/lsp/ tests/test_lsp.py
git commit -m "feat(lsp): minimal LSP integration for pyright and tsserver"
```

---

## Track G: Executor Integration (wires everything together)

### Task G1: Update executor to use FileService for reading existing files

**Objective:** The executor reads existing files before generating patch content, so it can emit surgical patches rather than guessing.

**Files:**
- Modify: `src/forge/runners/executor.py`

**Step 1: Read current executor.py (already done above — lines 1-200)**

**Step 2: Modify executor to inject FileService context**

Add to `run()` in `executor.py`:

```python
from forge.file_service import FileService

# Before LLM call, build file context from FileService
svc = FileService(project_workdir)
existing_files_context = ""

# Inject git status and key file contents
status = svc.status()
if status:
    changed_files = [f.path for f in status]
    existing_files_context += f"\n\nChanged files since HEAD: {', '.join(changed_files)}"

# For existing files in the spec that need editing, read their current content
# so the LLM can see what it needs to change
# (Pass this as context in the impl_prompt)
```

Then in `impl_prompt`, append:

```
{existing_files_context}
```

**Step 3: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/runners/executor.py
git commit -m "feat(executor): inject FileService context before generating patches"
```

---

### Task G2: Add permission check before file operations in executor

**Objective:** Before `write_files` or `apply_patch_files`, check permissions.

**Files:**
- Modify: `src/forge/runners/executor.py`
- Modify: `src/forge/runners/common.py`

**Step 3: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/runners/executor.py src/forge/runners/common.py
git commit -m "feat(executor): permission check before file writes"
```

---

### Task G3: Add context compaction trigger to graph runner

**Objective:** Before each LLM call, check if the session is approaching context overflow. If so, compact via `Compactor`.

**Files:**
- Modify: `src/forge/graph.py`

**Step 3: Commit**

```bash
cd /home/shaheen/forge
git add src/forge/graph.py
git commit -m "feat(graph): context compaction trigger before LLM calls"
```

---

## Testing & Integration

### Task Z1: Run full test suite

```bash
cd /home/shaheen/forge
/home/shaheen/.hermes/hermes-agent/venv/bin/python3.11 -m pytest tests/ -v --tb=short
```

### Task Z2: Smoke test with a real codebase

```bash
cd /tmp
mkdir test-project && cd test-project
git init
echo "def hello(): return 'Hello'" > hello.py
git add . && git commit -m "init"
forge new "Add a greet function that takes a name and returns 'Hello, {name}'" --project test
```

### Task Z3: Update SKILL.md to reflect v0.5 changes

Add to the skill doc:
- New files: `patch.py`, `retry.py`, `file_service.py`, `compactor.py`, `permissions.py`, `lsp/`
- Updated: `common.py` (patch action), `executor.py` (FileService + permissions), `llm.py` (retry)
- New CLI consideration: `forge plan` now uses the `plan` agent permission set

---

## Execution Order

1. **A1, A2, A3** — in parallel, each 5 min
2. **B1** — after A1
3. **C1** — after A2
4. **D1, E1, F1** — in parallel after A3
5. **B2** — after A1 (prompt update)
6. **G1, G2, G3** — after B and C and D/E/F
7. **Z1, Z2, Z3** — final integration

All tasks are independent subagent targets. The plan can be executed using `subagent-driven-development` with one subagent per track.
