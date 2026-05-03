import pytest
from pathlib import Path
from forge.lsp.service import LSPService
from forge.lsp.server import LSPServer, detect_servers

def test_lsp_service_initialization(tmp_path):
    svc = LSPService(tmp_path)
    assert svc.workdir == tmp_path

def test_lsp_detect_language_python(tmp_path):
    svc = LSPService(tmp_path)
    assert svc._detect_language("main.py") == "python"
    assert svc._detect_language("foo.py") == "python"

def test_lsp_detect_language_typescript(tmp_path):
    svc = LSPService(tmp_path)
    assert svc._detect_language("bar.ts") == "typescript"
    assert svc._detect_language("baz.tsx") == "typescript"
    assert svc._detect_language("qux.js") == "javascript"
    assert svc._detect_language("quux.mjs") == "javascript"

def test_lsp_server_available_check():
    """Test LSPServer.available() returns False for non-existent binaries."""
    server = LSPServer(id="fake", command=["nonexistent-binary-xyz"], extensions=[".py"])
    assert server.available() is False

def test_lsp_shutdown_called_without_error(tmp_path):
    """Test that shutdown() doesn't raise even if no servers were started."""
    svc = LSPService(tmp_path)
    svc.shutdown()  # Should not raise