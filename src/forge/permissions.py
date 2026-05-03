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
import fnmatch
import structlog
from dataclasses import dataclass
from enum import Enum
from typing import Optional

log = structlog.get_logger(__name__)


class PermissionResult(Enum):
    ALLOW = "allow"      # permitted without question
    DENY = "deny"        # immediately blocked
    ASK = "ask"          # require user confirmation


@dataclass
class PermissionRule:
    result: PermissionResult
    pattern: Optional[str] = None  # glob-like pattern for path matching


def _match_recursive(action: str, path: str, pattern: str) -> bool:
    """Match action:path against a pattern that may contain ** for recursive dirs."""
    target = f"{action}:{path}"
    # Handle ** as recursive directory matching (zero or more directories)
    if "**" in pattern:
        # Split pattern at ** - left side must match prefix, right side suffix
        parts = pattern.split("**", 1)
        prefix_pattern = parts[0]
        suffix_pattern = parts[1]
        
        # Prefix must match the start of target
        if prefix_pattern and not fnmatch.fnmatch(target, prefix_pattern + "*"):
            return False
        
        # Suffix must match somewhere in target
        if suffix_pattern:
            # Try matching at each possible position after the prefix
            search_start = len(prefix_pattern) if prefix_pattern else 0
            remaining = target[search_start:]
            # The suffix should match from the start of remaining after consuming dirs
            while remaining:
                if fnmatch.fnmatch(remaining, suffix_pattern.lstrip("/")):
                    return True
                # Consume one directory segment and try again
                if "/" in remaining:
                    remaining = remaining[remaining.index("/") + 1:]
                else:
                    remaining = ""
            return fnmatch.fnmatch(target, suffix_pattern.lstrip("/"))
        return True  # ** at end matches everything remaining
    
    return fnmatch.fnmatch(target, pattern)


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
        if rule.pattern is None:
            return True
        # Glob-style path matching - combine action and path for matching
        return _match_recursive(action, path, rule.pattern)

    @staticmethod
    def for_agent(agent: str) -> "PermissionSet":
        """Get the default permission set for an agent type."""
        base_rules: list[PermissionRule] = [
            # doom_loop always requires confirmation
            PermissionRule(PermissionResult.ASK, pattern="doom_loop*"),
            # External directory access is denied by default
            PermissionRule(PermissionResult.DENY, pattern="external_directory:*"),
        ]

        if agent == "build":
            return PermissionSet([
                # Specific .env rule comes BEFORE general read to take precedence
                # .env.example is explicitly allowed (safe variant)
                PermissionRule(PermissionResult.ALLOW, pattern="read:.env.example"),
                PermissionRule(PermissionResult.ASK, pattern="read:.env*"),
                PermissionRule(PermissionResult.ALLOW, pattern="read:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="write:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="patch:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="create:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="delete:*"),
                # External directory whitelist
                PermissionRule(PermissionResult.ALLOW, pattern="external_directory:/tmp/forge*"),
                PermissionRule(PermissionResult.ALLOW, pattern="external_directory:~/.forge*"),
            ] + base_rules)

        elif agent == "plan":
            return PermissionSet([
                PermissionRule(PermissionResult.ALLOW, pattern="read:*"),
                PermissionRule(PermissionResult.ALLOW, pattern="write:.opencode/plans/*"),
                PermissionRule(PermissionResult.ALLOW, pattern="write:**/plans/*.md"),
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