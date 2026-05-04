"""
forge.code_intelligence.git_ops — pygit2 wrapper for programmatic git operations.

pygit2 wraps libgit2 — same C library quality as gitoxide. Use this instead of
spawning `git` subprocess for any operation that requires reading object content,
traversing commits, or doing complex graph queries.

Usage:
    git = GitOps("/path/to/repo")
    diff = git.diff_head()
    commits = git.log(limit=10)
    status = git.status()
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)

# Lazy import — pygit2 is optional
_pygit2 = None


def _get_pygit2():
    global _pygit2
    if _pygit2 is None:
        import pygit2
        _pygit2 = pygit2
    return _pygit2


class GitError(Exception):
    """Raised when a git operation fails."""
    pass


@dataclass
class DiffEntry:
    """A single file changed between two commits."""
    old_path: str
    new_path: str
    status: str  # added, deleted, modified, renamed
    hunk_count: int = 0


@dataclass
class CommitInfo:
    """A single commit."""
    id: str  # hex SHA
    short_id: str
    message: str
    author: str
    author_email: str
    committed: str  # ISO timestamp
    parent_ids: list[str]


class GitOps:
    """
    pygit2-based git operations for forge agents.

    All operations are directly against the git object database — no subprocess,
    no string parsing, no shell quoting issues.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._repo = None
        self._discover()

    def _discover(self) -> None:
        """Open the git repository at self.path."""
        pygit2 = _get_pygit2()
        try:
            self._repo = pygit2.Repository(str(self.path))
            log.info("gitops.connected", path=str(self.path), head=self._repo.head.target.hex[:8] if self._repo.head.target else None)
        except pygit2.GitError as e:
            raise GitError(f"Not a git repository: {self.path}") from e

    @property
    def repo(self):
        """The underlying pygit2.Repository. Use when you need raw access."""
        return self._repo

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> list[DiffEntry]:
        """
        Return all files that differ from HEAD (working directory state).

        Uses pygit2's diff functionality for accurate, efficient results.
        """
        pygit2 = _get_pygit2()
        head = self._repo.head.target if self._repo.head.target else None
        head_tree = self._repo.get(head).tree if head else None

        workdir = self._repo.index
        workdir.refresh()

        diff = self._repo.diff(head_tree, workdir if hasattr(workdir, "diff") else None)

        # If no workdir diff, compute from workdir vs HEAD
        if diff.stats.total_changes == 0:
            try:
                worktree = self._repo.Workdir(self._repo.head.target.hex)
                diff = self._repo.diff(head_tree, worktree)
            except Exception:
                pass

        entries = []
        for p in diff:
            old_path = p.old_file.path if hasattr(p, "old_file") else getattr(p, "a_path", "?")
            new_path = p.new_file.path if hasattr(p, "new_file") else getattr(p, "b_path", "?")
            status_char = p.status_char() if hasattr(p, "status_char')") else "?"
            status_map = {
                "A": "added", "D": "deleted", "M": "modified",
                "R": "renamed", "C": "copied", "U": "unmerged",
            }
            status = status_map.get(status_char, "modified")
            entries.append(DiffEntry(
                old_path=str(old_path),
                new_path=str(new_path),
                status=status,
            ))
        return entries

    def staged(self) -> list[DiffEntry]:
        """Return all files in the git index (staged changes)."""
        pygit2 = _get_pygit2()
        head = self._repo.head.target if self._repo.head.target else None
        head_tree = self._repo.get(head).tree if head else None

        try:
            index = self._repo.index
            index.refresh()
            diff = self._repo.diff(head_tree, index)
        except Exception:
            return []

        entries = []
        for p in diff:
            old_path = getattr(p, "old_file", None)
            new_path = getattr(p, "new_file", None)
            if old_path and new_path:
                entries.append(DiffEntry(
                    old_path=old_path.path,
                    new_path=new_path.path,
                    status="staged",
                ))
        return entries

    # ── Diff ────────────────────────────────────────────────────────────────

    def diff_head(self, file_filter: Optional[str] = None) -> str:
        """
        Return a unified diff string of working tree vs HEAD.

        Args:
            file_filter: If set, only diff this file path.

        Returns:
            Unified diff string (same as `git diff HEAD`).
        """
        pygit2 = _get_pygit2()
        head = self._repo.head.target if self._repo.head.target else None
        if not head:
            return ""

        head_tree = self._repo.get(head).tree
        worktree = self._repo.Workdir(target=head)

        diff = self._repo.diff(head_tree, worktree)

        if file_filter:
            # Filter to a specific path
            deltas = [d for d in diff if file_filter in (d.old_file.path or "") or file_filter in (d.new_file.path or "")]
        else:
            deltas = list(diff)

        # Render as unified diff
        lines = []
        for delta in deltas:
            patch = self._repo.diff(head_tree, worktree, patches=[delta])
            for hunk in patch:
                lines.append(str(hunk))
        return "\n".join(lines)

    def diff_commits(self, from_commit: str, to_commit: str) -> str:
        """
        Return unified diff between two commits.

        Args:
            from_commit: Starting commit SHA (or "HEAD~N").
            to_commit: Ending commit SHA.
        """
        pygit2 = _get_pygit2()

        def resolve(ref):
            try:
                return self._repo.revparse_single(ref)
            except Exception:
                return None

        from_obj = resolve(from_commit)
        to_obj = resolve(to_commit)
        if not from_obj or not to_obj:
            return ""

        diff = self._repo.diff(from_obj.tree, to_obj.tree)
        lines = []
        for delta in diff:
            patch = self._repo.diff(from_obj.tree, to_obj.tree, patches=[delta])
            for hunk in patch:
                lines.append(str(hunk))
        return "\n".join(lines)

    # ── Log ─────────────────────────────────────────────────────────────────

    def log(self, limit: int = 50, path: Optional[str] = None) -> list[CommitInfo]:
        """
        Return the commit history, optionally filtered to a path.

        Args:
            limit: Max number of commits to return.
            path: If set, only commits touching this file/dir.
        """
        pygit2 = _get_pygit2()
        commits = []

        try:
            for commit in self._repo.walk(self._repo.head.target, pygit2.GIT_SORT_TIME):
                if limit and len(commits) >= limit:
                    break

                if path:
                    # Check if this commit touched the path
                    tree = commit.tree
                    try:
                        entry = tree[path]
                        del entry  # just check existence
                    except KeyError:
                        continue

                commits.append(CommitInfo(
                    id=commit.hex,
                    short_id=commit.hex[:8],
                    message=commit.message.strip(),
                    author=commit.author.name,
                    author_email=commit.author.email,
                    committed=commit.commit_time,
                    parent_ids=[p.hex for p in commit.parents],
                ))

                if len(commits) >= limit:
                    break
        except Exception as e:
            log.warning("gitops.log_failed", error=str(e))

        return commits

    # ── Blame ───────────────────────────────────────────────────────────────

    def blame(self, file_path: str) -> list[tuple[str, str, int]]:
        """
        Return line-by-line blame info for a file.

        Returns:
            List of (commit_sha, line_content, line_number).
        """
        pygit2 = _get_pygit2()
        try:
            blame = self._repo.blame(file_path)
        except Exception:
            return []

        results = []
        for hunk in blame:
            for line in hunk.lines:
                results.append((
                    hunk.final_commit_id.hex if hasattr(hunk, "final_commit_id") else "?",
                    line.content.rstrip("\n"),
                    line.old_lineno,
                ))
        return results

    # ── Read file at commit ─────────────────────────────────────────────────

    def file_at_commit(self, file_path: str, commit_ref: str = "HEAD") -> Optional[bytes]:
        """
        Read the content of a file at a specific commit.

        Args:
            file_path: Path relative to repo root.
            commit_ref: Commit SHA or ref.

        Returns:
            File content as bytes, or None if not found.
        """
        try:
            commit = self._repo.revparse_single(commit_ref)
            tree = commit.tree
            entry = tree[file_path]
            obj = self._repo.get(entry.id)
            return obj.data if hasattr(obj, "data") else None
        except Exception:
            return None

    # ── Branch helpers ─────────────────────────────────────────────────────

    def branches(self) -> list[str]:
        """Return all local branch names."""
        return [b.name for b in self._repo.branches]

    def current_branch(self) -> str:
        """Return the current branch name."""
        return self._repo.head.shorthand if self._repo.head.target else ""

    def is_dirty(self) -> bool:
        """Return True if there are uncommitted changes."""
        return bool(self.status())

    # ── Commit helpers ─────────────────────────────────────────────────────

    def create_commit(
        self,
        message: str,
        author_name: str,
        author_email: str,
        *,
        files: Optional[list[tuple[str, str]]] = None,  # (path, content) pairs
        amend: bool = False,
    ) -> str:
        """
        Create a new commit with the given message and optional file changes.

        Args:
            message: Commit message.
            author_name: Author display name.
            author_email: Author email.
            files: List of (repo_relative_path, content) to write before committing.
            amend: If True, amend the current HEAD commit instead of creating a new one.

        Returns:
            The new commit SHA.
        """
        pygit2 = _get_pygit2()
        ref = "HEAD"
        tree_builder = self._repo.TreeBuilder()

        # Apply file changes to the tree
        if files:
            for file_path, content in files:
                blob_oid = self._repo.create_blob(content.encode("utf-8"))
                tree_builder.insert(file_path, blob_oid, pygit2.GIT_FILEMODE_BLOB)

        try:
            new_tree = tree_builder.write()

            # Get parent commit(s)
            parents = []
            if self._repo.head.target:
                parents = [self._repo.head.target]

            signature = pygit2.Signature(
                name=author_name,
                email=author_email,
                time=int(__import__("time").time()),
            )

            commit_id = self._repo.create_commit(
                ref,
                signature,
                signature,
                message,
                new_tree,
                parents,
            )
            return str(commit_id)
        except Exception as e:
            raise GitError(f"create_commit failed: {e}") from e
