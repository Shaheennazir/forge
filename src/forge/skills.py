"""
forge.skills — Skill registry and loader.

Skills are discovered from:
  - ~/.forge/skills/            (user skills)
  - ~/.hermes/skills/          (shared with Hermes Agent, if exists)

Each skill is a directory containing SKILL.md.
"""

from __future__ import annotations
import yaml
import structlog
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

log = structlog.get_logger(__name__)

SKILL_MARKER = "SKILL.md"


@dataclass
class Skill:
    name: str
    description: str
    category: Optional[str]
    path: Path
    metadata: dict


class SkillRegistry:
    """
    Discovers and loads skills on demand.
    Task type → matching skill(s) are loaded into execution context.
    """

    def __init__(self, extra_paths: list[Path] | None = None):
        self.skills: dict[str, Skill] = {}
        self.search_paths: list[Path] = [
            Path.home() / ".forge" / "skills",
            Path.home() / ".hermes" / "skills",
        ]
        if extra_paths:
            self.search_paths.extend(extra_paths)
        self._scan()

    def _scan(self):
        for base in self.search_paths:
            if not base.exists():
                continue
            for item in base.rglob(SKILL_MARKER):
                skill_dir = item.parent
                try:
                    skill = self._load_skill(skill_dir)
                    self.skills[skill.name] = skill
                    log.debug("skill.discovered", name=skill.name, path=str(skill_dir))
                except Exception as e:
                    log.warning("skill.load_error", path=str(skill_dir), error=str(e))

    def _load_skill(self, path: Path) -> Skill:
        with open(path / SKILL.md) as f:
            raw = f.read()
        # Parse frontmatter
        if raw.startswith("---"):
            end = raw.index("---", 3)
            frontmatter = yaml.safe_load(raw[3:end]) or {}
            body = raw[end + 3:].strip()
        else:
            frontmatter = {}
            body = raw
        name = frontmatter.get("name", path.name)
        return Skill(
            name=name,
            description=frontmatter.get("description", ""),
            category=frontmatter.get("category"),
            path=path,
            metadata=frontmatter,
        )

    def get(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def match(self, task_type: str) -> list[Skill]:
        """
        Match skills to a task type.
        Exact name match first, then category prefix match.
        """
        results = []
        # Exact
        if task_type in self.skills:
            results.append(self.skills[task_type])
        # Category
        for skill in self.skills.values():
            if skill.category and task_type.startswith(skill.category):
                if skill not in results:
                    results.append(skill)
        return results

    def list_all(self) -> list[Skill]:
        return list(self.skills.values())


def load_skill_content(skill: Skill) -> str:
    """Load full SKILL.md content for a skill."""
    with open(skill.path / SKILL.md) as f:
        return f.read()
