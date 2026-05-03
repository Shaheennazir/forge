import pytest
from forge.permissions import PermissionSet, PermissionResult, check_permission

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

def test_external_directory_requires_ask_for_etc():
    ps = PermissionSet.for_agent("build")
    assert ps.allows("external_directory", "/etc/passwd") == PermissionResult.DENY

def test_external_directory_whitelisted():
    ps = PermissionSet.for_agent("build")
    assert ps.allows("external_directory", "/tmp/forge-workspace/foo") == PermissionResult.ALLOW
    assert ps.allows("external_directory", "~/.forge/projects/bar") == PermissionResult.ALLOW

def test_review_agent_allows_read_and_patch():
    ps = PermissionSet.for_agent("review")
    assert ps.allows("read", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("patch", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("write", "src/foo.py") == PermissionResult.DENY
    assert ps.allows("create", "src/foo.py") == PermissionResult.DENY

def test_unknown_agent_is_read_only():
    ps = PermissionSet.for_agent("unknown-agent")
    assert ps.allows("read", "src/foo.py") == PermissionResult.ALLOW
    assert ps.allows("write", "src/foo.py") == PermissionResult.DENY
    assert ps.allows("patch", "src/foo.py") == PermissionResult.DENY

def test_check_permission_convenience():
    assert check_permission("build", "read", "foo.py") == PermissionResult.ALLOW
    assert check_permission("plan", "write", "foo.py") == PermissionResult.DENY