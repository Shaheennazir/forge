import pytest, subprocess
from pathlib import Path
from forge.file_service import FileService, FileType, FileInfo

@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    # Create initial commit
    (tmp_path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_read_text_file(git_repo):
    (git_repo / "hello.txt").write_text("Hello, world!\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add hello"], cwd=git_repo, check=True, capture_output=True)

    svc = FileService(git_repo)
    content = svc.read("hello.txt")
    assert content.type == FileType.TEXT
    assert content.content == "Hello, world!\n"


def test_read_nonexistent_file_returns_empty_text(git_repo):
    svc = FileService(git_repo)
    content = svc.read("does_not_exist.py")
    assert content.type == FileType.TEXT
    assert content.content == ""


def test_status_modified_file(git_repo):
    (git_repo / "foo.txt").write_text("foo\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True, capture_output=True)

    (git_repo / "foo.txt").write_text("foo modified\n")

    svc = FileService(git_repo)
    status = svc.status()
    modified = [f for f in status if "modified" in f.path or f.diff]
    assert any("foo.txt" in f.path for f in status)


def test_status_untracked_file(git_repo):
    (git_repo / "newfile.txt").write_text("new\n")
    svc = FileService(git_repo)
    status = svc.status()
    assert any("newfile.txt" in f.path for f in status)


def test_search_files(git_repo):
    (git_repo / "alpha.py").write_text("alpha")
    (git_repo / "beta.py").write_text("beta")
    (git_repo / "gamma.py").write_text("gamma")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True, capture_output=True)

    svc = FileService(git_repo)
    results = svc.search("alpha")
    assert "alpha.py" in results
    assert "beta.py" not in results


def test_list_directory(git_repo):
    (git_repo / "src").mkdir()
    (git_repo / "src" / "main.py").write_text("main")
    (git_repo / "tests").mkdir()
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=git_repo, check=True, capture_output=True)

    svc = FileService(git_repo)
    nodes = svc.list()
    names = [n.name for n in nodes]
    assert "src" in names


def test_file_info_dataclass():
    fi = FileInfo(path="foo.py", type=FileType.TEXT, content="print('hi')")
    assert fi.path == "foo.py"
    assert fi.type == FileType.TEXT
    assert fi.content == "print('hi')"