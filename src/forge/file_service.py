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
    diff: str = ""
    encoding: Optional[str] = None

    @property
    def name(self) -> str:
        return Path(self.path).name


class GitNotAvailable(ValueError):
    """Raised when the directory is not a git repository."""
    pass


class FileService:
    """Git-aware file operations for the executor."""

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
        cfg = ["-c", "core.autocrlf=false", "-c", "core.fsmonitor=false", "-c", "core.quotepath=false"]
        return subprocess.run(
            ["git"] + cfg + args,
            cwd=str(self.workdir),
            capture_output=True, text=True, check=check,
        )

    def _is_binary(self, path: Path) -> bool:
        return path.suffix.lstrip(".") in self._binary_extensions

    def read(self, file: str) -> FileInfo:
        """Read a file, returning text content and git diff against HEAD."""
        full = self.workdir / file if not Path(file).is_absolute() else Path(file)
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
                ft = FileType.BINARY if self._is_binary(Path(path)) else FileType.TEXT
                changed.append(FileInfo(
                    path=path, type=ft,
                    content=f"+{added}/-{removed}",
                ))

        untracked = self._run_git(["ls-files", "--others", "--exclude-standard"], check=False)
        if untracked.stdout.strip():
            for path in untracked.stdout.strip().split("\n"):
                if path:
                    changed.append(FileInfo(path=path, type=FileType.TEXT))

        deleted = self._run_git(["diff", "--name-only", "--diff-filter=D", "HEAD"], check=False)
        if deleted.stdout.strip():
            for path in deleted.stdout.strip().split("\n"):
                if path:
                    changed.append(FileInfo(path=path, type=FileType.TEXT))

        return changed

    def search(self, query: str, limit: int = 100) -> list[str]:
        """Fuzzy search for files by name."""
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.workdir), capture_output=True, text=True, check=True,
            )
            all_files = [f for f in result.stdout.strip().split("\n") if f]
        except subprocess.CalledProcessError:
            all_files = []
            for p in self.workdir.rglob("*"):
                if p.is_file() and not any(part.startswith(".") for part in p.parts):
                    all_files.append(str(p.relative_to(self.workdir)))

        query_lower = query.lower()
        scored = []
        for f in all_files:
            name = Path(f).name.lower()
            if query_lower in name:
                score = 0 if name.startswith(query_lower) else 1
                scored.append((score, f))
        scored.sort(key=lambda x: x[0])
        return [f for _, f in scored[:limit]]

    def list(self, dir: str = ".") -> list[FileInfo]:
        """List files and directories at a given path."""
        full = self.workdir / dir
        if not full.exists():
            return []

        results = []
        for entry in full.iterdir():
            rel = entry.relative_to(self.workdir)
            if entry.name.startswith("."):
                continue
            results.append(FileInfo(
                path=str(rel),
                type=FileType.TEXT,
            ))
        return sorted(results, key=lambda x: x.path)