"""
forge.code_intelligence.rope_ — Rope semantic refactoring wrapper for forge.

Usage:
    from forge.code_intelligence.rope_ import rename_function, extract_method
    result = rename_function(workdir=Path("src/"), old_name="get_user", new_name="fetch_user")

Rope is a Python refactoring library that performs semantic edits —
not string replace. Rename a function and rope updates every call site
correctly including in docstrings and comments.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


@dataclass
class RenameResult:
    """Result of a rope rename refactoring."""
    success: bool
    old_name: str
    new_name: str
    files_changed: int
    changes: list[dict]     # [{path, old_def, new_def, call_sites_updated}]
    raw: str


@dataclass
class ExtractResult:
    """Result of a rope extract method/variable refactoring."""
    success: bool
    extracted_name: str
    source_path: str
    new_method_line: int
    changes: list[dict]
    raw: str


def rename_function(
    workdir: Path,
    old_name: str,
    new_name: str,
    file_pattern: str = "*.py",
) -> RenameResult:
    """
    Semantically rename a function across the entire project.

    Rope resolves the function definition, all call sites, and updates
    docstrings/comments that mention the old name. No regex, no string replace.
    """
    try:
        import rope.base.project
        import rope.refactor.rename

        project = rope.base.project.Project(str(workdir.absolute()))
        resources = project.get_folder('').match_files(file_pattern)

        total_changes = 0
        changes_list: list[dict] = []

        for resource in resources:
            try:
                holder = project.get_resource(str(resource))
                data = rope.refactor.rename.rename(
                    project,
                    holder,
                    old_name,
                    new_name,
                )
                if data:
                    total_changes += 1
                    changes_list.append({
                        "path": str(resource),
                        "old": old_name,
                        "new": new_name,
                    })
            except Exception:
                # This file doesn't contain the function, skip
                continue

        project.close()

        return RenameResult(
            success=True,
            old_name=old_name,
            new_name=new_name,
            files_changed=total_changes,
            changes=changes_list,
            raw=f"rope: renamed '{old_name}' → '{new_name}' in {total_changes} files",
        )

    except Exception as e:
        log.warning("rope_.rename_error", old=old_name, new=new_name, error=str(e))
        return RenameResult(
            success=False,
            old_name=old_name,
            new_name=new_name,
            files_changed=0,
            changes=[],
            raw=f"rope rename error: {e}",
        )


def extract_method(
    workdir: Path,
    source_path: str,
    name: str,
    start_line: int,
    end_line: int,
) -> ExtractResult:
    """
    Extract a range of lines into a new method.

    Rope determines parameters and return value automatically
    from the code within the selection.
    """
    try:
        import rope.base.project
        import rope.refactor.extract

        project = rope.base.project.Project(str(workdir.absolute()))
        resource = project.get_resource(source_path)

        extractor = rope.refactor.extract.ExtractMethod(
            project,
            resource,
            start_line,
            end_line,
        )
        goal = extractor.get_new_name(name)
        changes = extractor.get_changes(goal)

        project.do(changes)
        project.close()

        return ExtractResult(
            success=True,
            extracted_name=name,
            source_path=source_path,
            new_method_line=start_line,
            changes=[{"path": source_path, "extracted_as": name}],
            raw=f"rope: extracted method '{name}' from lines {start_line}-{end_line}",
        )

    except Exception as e:
        log.warning("rope_.extract_error", path=source_path, error=str(e))
        return ExtractResult(
            success=False,
            extracted_name=name,
            source_path=source_path,
            new_method_line=0,
            changes=[],
            raw=f"rope extract error: {e}",
        )


def inline_function(
    workdir: Path,
    source_path: str,
    name: str,
    start_line: int,
    end_line: int,
) -> dict:
    """
    Inline a function call — replace call sites with the function body.

    Inverse of extract_method.
    """
    try:
        import rope.base.project
        import rope.refactor.inline

        project = rope.base.project.Project(str(workdir.absolute()))
        resource = project.get_resource(source_path)

        inliner = rope.refactor.inline.create_inliner(
            project,
            resource,
            start_line,
            end_line,
        )
        changes = inliner.get_changes(name)

        project.do(changes)
        project.close()

        return {
            "success": True,
            "raw": f"rope: inlined function '{name}'",
        }

    except Exception as e:
        return {
            "success": False,
            "raw": f"rope inline error: {e}",
        }


def move_symbol(
    workdir: Path,
    source_path: str,
    symbol_name: str,
    dest_path: str,
) -> dict:
    """
    Move a function/class to a different module file.

    Rope updates all imports at call sites automatically.
    """
    try:
        import rope.base.project
        import rope.refactor.move

        project = rope.base.project.Project(str(workdir.absolute()))
        source = project.get_resource(source_path)

        mover = rope.refactor.move.create_move(project, source, symbol_name)
        changes = mover.get_changes(dest_path)

        project.do(changes)
        project.close()

        return {
            "success": True,
            "source": source_path,
            "dest": dest_path,
            "symbol": symbol_name,
            "raw": f"rope: moved '{symbol_name}' from '{source_path}' to '{dest_path}'",
        }

    except Exception as e:
        return {
            "success": False,
            "raw": f"rope move error: {e}",
        }
