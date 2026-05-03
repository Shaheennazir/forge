from pathlib import Path


def test_apply_patch_files_surgical_edit(tmp_path):
    """Test that apply_patch_files applies surgical edits to existing files."""
    from forge.patch import parse_patch
    from forge.runners.common import apply_patch_files

    foo = tmp_path / "foo.py"
    foo.write_text("line1\nline2\nline3\nline4\nline5\n")

    patch_text = """*** Begin Patch
*** Update File: foo.py
@@ line2
-line2
+line2 modified
*** End Patch"""

    hunks = parse_patch(patch_text)["hunks"]
    result = apply_patch_files(hunks, tmp_path)

    lines = foo.read_text().splitlines()
    assert lines == ["line1", "line2 modified", "line3", "line4", "line5"]
    assert Path(result["modified"][0]).name == "foo.py"


def test_apply_patch_files_add_new_file(tmp_path):
    """Test that apply_patch_files can add new files."""
    from forge.patch import parse_patch
    from forge.runners.common import apply_patch_files

    patch_text = """*** Begin Patch
*** Add File: src/new.py
+class Foo:
+    pass
*** End Patch"""

    hunks = parse_patch(patch_text)["hunks"]
    result = apply_patch_files(hunks, tmp_path)

    assert (tmp_path / "src" / "new.py").exists()
    assert Path(result["added"][0]).name == "new.py"


def test_apply_patch_files_delete_file(tmp_path):
    """Test that apply_patch_files can delete files."""
    from forge.patch import parse_patch
    from forge.runners.common import apply_patch_files

    garbage = tmp_path / "garbage.py"
    garbage.write_text("delete me\n")

    patch_text = """*** Begin Patch
*** Delete File: garbage.py
*** End Patch"""

    hunks = parse_patch(patch_text)["hunks"]
    result = apply_patch_files(hunks, tmp_path)

    assert not garbage.exists()
    assert Path(result["deleted"][0]).name == "garbage.py"
