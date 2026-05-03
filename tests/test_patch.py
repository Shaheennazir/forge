import pytest
from forge.patch import parse_patch, apply_patch, PatchHunk, UpdateFileChunk, seek_sequence

def test_parse_add_file():
    text = """*** Begin Patch
*** Add File: src/new.py
+class Foo:
+    pass
*** End Patch"""
    result = parse_patch(text)
    assert len(result["hunks"]) == 1
    assert result["hunks"][0].type.value == "add"
    assert result["hunks"][0].path == "src/new.py"
    assert "class Foo" in result["hunks"][0].contents

def test_parse_update_hunks():
    text = """*** Begin Patch
*** Update File: src/foo.py
@@ old line 1
-old line
+added line
  context line
*** End Patch"""
    result = parse_patch(text)
    hunk = result["hunks"][0]
    assert hunk.type.value == "update"
    assert hunk.path == "src/foo.py"
    assert len(hunk.chunks) == 1
    assert hunk.chunks[0].old_lines == ["old line"]
    assert hunk.chunks[0].new_lines == ["added line"]

def test_parse_delete_file():
    text = """*** Begin Patch
*** Delete File: src/garbage.py
*** End Patch"""
    result = parse_patch(text)
    assert result["hunks"][0].type.value == "delete"
    assert result["hunks"][0].path == "src/garbage.py"

def test_surgical_edit_apply(tmp_path):
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

def test_seek_sequence_exact():
    lines = ["foo", "bar", "baz"]
    assert seek_sequence(lines, ["bar"], 0) == 1
    assert seek_sequence(lines, ["foo"], 0) == 0
    assert seek_sequence(lines, ["baz"], 0) == 2

def test_seek_sequence_rstrip():
    """Pass 2: rstrip whitespace before comparing."""
    lines = ["foo  ", "bar", "baz"]
    assert seek_sequence(lines, ["foo"], 0) == 0
    assert seek_sequence(lines, ["bar  "], 0) == 1

def test_seek_sequence_trim():
    """Pass 3: trim leading/trailing whitespace."""
    lines =["  foo  ", "bar", "baz"]
    assert seek_sequence(lines, ["foo"], 0) == 0
    assert seek_sequence(lines, ["  bar"], 0) == 1

def test_seek_sequence_not_found():
    lines = ["foo", "bar", "baz"]
    assert seek_sequence(lines, ["qux"], 0) == -1

def test_seek_sequence_start_idx():
    lines = ["foo", "bar", "foo", "baz"]
    assert seek_sequence(lines, ["foo"], 2) == 2

def test_apply_add_file(tmp_path):
    text = """*** Begin Patch
*** Add File: src/new.py
+class Foo:
+    pass
*** End Patch"""
    hunks = parse_patch(text)["hunks"]
    apply_patch(hunks, tmp_path)
    assert (tmp_path / "src" / "new.py").read_text() == "class Foo:\n    pass\n"

def test_apply_delete_file(tmp_path):
    garbage = tmp_path / "garbage.py"
    garbage.write_text("delete me\n")
    text = """*** Begin Patch
*** Delete File: garbage.py
*** End Patch"""
    hunks = parse_patch(text)["hunks"]
    apply_patch(hunks, tmp_path)
    assert not garbage.exists()

def test_apply_patch_returns_summary(tmp_path):
    foo = tmp_path / "foo.py"
    foo.write_text("line1\nline2\nline3\n")
    text = """*** Begin Patch
*** Update File: foo.py
@@ line2
-line2
+line2 modified
*** End Patch"""
    hunks = parse_patch(text)["hunks"]
    result = apply_patch(hunks, tmp_path)
    assert "modified" in result
    assert any("foo.py" in p for p in result["modified"])