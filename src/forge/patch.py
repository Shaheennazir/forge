from __future__ import annotations
import re
import structlog
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

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


def parse_patch(patch_text: str) -> dict:
    """Parse the custom patch format into structured hunks."""
    hunks: list[PatchHunk] = []
    
    # Strip any heredoc wrapper
    patch_text = re.sub(r'^.*?\*\*\* Begin Patch\s*', '', patch_text, flags=re.DOTALL)
    patch_text = re.sub(r'\*\*\* End Patch.*', '', patch_text)
    
    # Split into sections
    sections = re.split(r'^\*\*\* (Add|Update|Delete) File: (.+)$', patch_text, flags=re.MULTILINE)
    # sections[0] is empty, then (type, path, body) triplets follow
    
    i = 0
    while i < len(sections):
        if not sections[i].strip():
            i += 1
            continue
        if i + 2 >= len(sections):
            break
        file_type = sections[i].strip()
        path = sections[i+1].strip()
        body = sections[i+2] if i+2 < len(sections) else ""
        
        if file_type == "Add":
            # Collect + lines
            lines = []
            for line in body.splitlines():
                if line.startswith("+"):
                    lines.append(line[1:])
            hunk = PatchHunk(type=HunkType.ADD, path=path, contents="\n".join(lines) + "\n")
            hunks.append(hunk)
        elif file_type == "Delete":
            hunk = PatchHunk(type=HunkType.DELETE, path=path)
            hunks.append(hunk)
        elif file_type == "Update":
            hunk = PatchHunk(type=HunkType.UPDATE, path=path)
            # Parse @@ chunks
            chunk_pattern = re.compile(r'^@@ (.+)\n((?:[-+ ].+\n)*)', re.MULTILINE)
            for m in chunk_pattern.finditer(body):
                ctx = m.group(1)
                chunk_text = m.group(2)
                old_lines = []
                new_lines = []
                eof = False
                for cline in chunk_text.splitlines():
                    if cline.startswith("-") and not cline.startswith("---"):
                        old_lines.append(cline[1:])
                    elif cline.startswith("+") and not cline.startswith("+++"):
                        new_lines.append(cline[1:])
                    elif cline.startswith("@@"):
                        # Special case: @@ at end of file = EOF anchor
                        if "end of file" in cline.lower() or ctx.strip() == "@@":
                            eof = True
                hunk.chunks.append(UpdateFileChunk(
                    old_lines=old_lines, new_lines=new_lines,
                    change_context=ctx, is_end_of_file=eof
                ))
            hunks.append(hunk)
        i += 3
    
    return {"hunks": hunks}


def seek_sequence(lines: list[str], pattern: list[str], start_idx: int, eof: bool = False) -> int:
    """Find pattern in lines using 4-pass matching.
    
    Pass 1: exact string equality
    Pass 2: rstrip() equality
    Pass 3: strip() equality  
    Pass 4: normalized unicode (replace smart quotes, etc.)
    
    Returns index or -1 if not found.
    """
    def normalize(s: str) -> str:
        return s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    
    if eof:
        # Try matching from end first
        for delta in range(len(pattern)):
            idx = len(lines) - len(pattern) - delta
            if idx < 0:
                idx = 0
            found = _match_at(lines, pattern, idx, normalize)
            if found:
                return idx
    
    # Scan from start_idx
    for i in range(start_idx, len(lines) - len(pattern) + 1):
        if _match_at(lines, pattern, i, normalize):
            return i
    return -1


def _match_at(lines: list[str], pattern: list[str], idx: int, normalize) -> bool:
    """Try all 4 passes at a specific index."""
    for pi in range(len(pattern)):
        li = idx + pi
        if li >= len(lines):
            return False
        target = pattern[pi]
        # Pass 1: exact
        if lines[li] == target:
            continue
        # Pass 2: rstrip
        if lines[li].rstrip() == target.rstrip():
            continue
        # Pass 3: strip
        if lines[li].strip() == target.strip():
            continue
        # Pass 4: normalized
        if normalize(lines[li].strip()) == normalize(target.strip()):
            continue
        return False
    return True


def compute_replacements(original_lines: list[str], chunks: list[UpdateFileChunk]) -> list[tuple[int, int, list[str]]]:
    """Compute (start, old_len, new_lines) replacements for surgical edit."""
    replacements = []
    offset = 0
    
    for chunk in chunks:
        if chunk.is_end_of_file:
            # Addition at EOF
            start = len(original_lines)
            replacements.append((start, 0, chunk.new_lines))
            continue
        
        pattern = chunk.old_lines if chunk.old_lines else chunk.new_lines
        if not pattern:
            continue
        
        # Find where this chunk's old_lines appear
        idx = seek_sequence(original_lines, pattern, 0)
        if idx == -1:
            log.warning("patch.chunk_not_found", pattern=pattern[:1])
            continue
        
        old_len = len(chunk.old_lines) if chunk.old_lines else 0
        
        # Preserve line ending from original if new_lines is provided
        new_lines = list(chunk.new_lines)
        if new_lines and original_lines:
            # Get the line that should follow the old lines (to check trailing newline)
            end_idx = idx + old_len - 1 if old_len > 0 else idx
            if end_idx < len(original_lines):
                end_line = original_lines[end_idx]
                if end_line.endswith('\n') and new_lines:
                    # Preserve the trailing newline from the original
                    new_lines[-1] = new_lines[-1] + '\n'
        
        replacements.append((idx, old_len, new_lines))
    
    # Sort descending so we can apply from top to bottom
    replacements.sort(key=lambda x: x[0], reverse=True)
    return replacements


def apply_replacements(lines: list[str], replacements: list[tuple[int, int, list[str]]]) -> list[str]:
    """Apply sorted (start, old_len, new_lines) replacements."""
    result = list(lines)
    for start, old_len, new_lines in replacements:
        result[start:start+old_len] = new_lines
    return result


def derive_new_contents(original: str, chunks: list[UpdateFileChunk]) -> tuple[str, str]:
    """Derive new file contents from surgical chunks. Returns (new_content, diff)."""
    import difflib
    lines = original.splitlines(keepends=True)
    
    replacements = compute_replacements(original_lines=lines, chunks=chunks)
    new_lines = apply_replacements(lines, replacements)
    
    # Generate unified diff
    diff = "".join(difflib.unified_diff(
        lines, new_lines,
        fromfile="a", tofile="b",
        lineterm=""
    ))
    
    return "".join(new_lines), diff


def apply_patch(hunks: list[PatchHunk], workdir: Path) -> dict:
    """Apply patch hunks to filesystem. Returns {added, modified, deleted}."""
    added, modified, deleted = [], [], []
    
    for hunk in hunks:
        path = workdir / hunk.path
        
        if hunk.type == HunkType.ADD:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(hunk.contents)
            added.append(str(path))
            
        elif hunk.type == HunkType.DELETE:
            if path.exists():
                path.unlink()
            deleted.append(str(path))
            
        elif hunk.type == HunkType.UPDATE:
            if not path.exists():
                log.warning("patch.update_nonexistent", path=str(path))
                continue
            original = path.read_text()
            new_content, _ = derive_new_contents(original, hunk.chunks)
            path.write_text(new_content)
            modified.append(str(path))
    
    return {"added": added, "modified": modified, "deleted": deleted}