"""forge.runners.spec_gen — Spec Generator node runner."""

from __future__ import annotations
import structlog
from forge.graph import ForgeGraph, NodeStatus
from forge.db import ForgeDB
from forge.skills import SkillRegistry
from forge.runners.common import (
    ShortTermMemory,
    SPEC_GEN_SYSTEM,
    build_spec_changelog,
    generate_stub_spec,
)

log = structlog.get_logger(__name__)


def inject_skills_into_context(
    task_type: str,
    skill_registry: SkillRegistry,
) -> list[str]:
    """Match and return skill summaries for a task type."""
    from forge.skills import Skill

    matched = skill_registry.match(task_type)
    return [f"[skill:{s.name}] {s.description}" for s in matched]


def run(
    g: ForgeGraph,
    db: ForgeDB,
    node_id: str,
    stm: ShortTermMemory,
    prompt: str,
    skill_registry: SkillRegistry | None = None,
    **extra,
) -> dict:
    """
    Spec Generator: produces SPEC.md with optional skill context.

    Reads existing spec version, updates it with new prompt,
    saves as a new version row (append-only changelog).
    """
    log.info("spec_gen.run", prompt=prompt[:100])

    latest_ver = db.latest_spec_version()
    existing_spec_md: str | None = None
    existing_version: int | None = None
    if latest_ver:
        sv = db.get_spec_version(latest_ver)
        if sv:
            existing_spec_md = sv.spec_md
            existing_version = latest_ver

    spec_md: str
    version = (existing_version or 0) + 1

    if g.llm:
        system = SPEC_GEN_SYSTEM
        if skill_registry:
            skill_summaries = inject_skills_into_context("planning", skill_registry)
            if skill_summaries:
                system = (
                    SPEC_GEN_SYSTEM
                    + "\n\nRelevant skills:\n"
                    + "\n".join(skill_summaries)
                )

        llm_prompt = prompt
        if existing_spec_md:
            llm_prompt = (
                f"Update this existing SPEC.md based on the new request.\n\n"
                f"Current spec:\n{existing_spec_md}\n\n"
                f"New request: {prompt}"
            )

        spec_md = g.llm.complete(
            prompt=llm_prompt,
            system=system,
            max_tokens=4096,
            temperature=0.3,
        )
        changelog = build_spec_changelog(prompt, spec_md, existing_spec_md)
    else:
        spec_md = generate_stub_spec(prompt)
        changelog = build_spec_changelog(prompt, spec_md, existing_spec_md)

    db.save_spec_version(version, prompt, spec_md, changelog)
    db.write_memory(
        tier="mid", agent="spec_gen", key="current_spec_version", value=str(version)
    )
    db.write_memory(
        tier="mid", agent="spec_gen", key="current_spec_md", value=spec_md
    )

    g.update_node_status(node_id, NodeStatus.DONE)
    output = {"spec_version": version, "spec_md": spec_md}
    g.store_output(node_id, output)
    return output
