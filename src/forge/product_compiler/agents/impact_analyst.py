"""
forge.product_compiler.agents.impact_analyst — Blast-radius analysis for edit mode.

Stage 2 of the edit pipeline: given a change intent and the current codebase,
identifies the precise surface area that the change will affect.

Uses jedi for semantic call-graph analysis (find all callers of a function)
and rope for safe refactoring (rename, move, extract).
"""

from __future__ import annotations

import structlog
from pathlib import Path

from forge.llm import LLMBackend
from forge.product_compiler.models import (
    ChangeIntent,
    ImpactSurface,
    BlastRadiusResult,
    SemanticRefactorResult,
)

log = structlog.get_logger(__name__)


class ImpactAnalystAgent:
    """
    Analyzes blast radius of proposed changes using semantic tools.

    jedi: find all callers of a function across the entire project
    rope: perform semantic refactors (rename, move, extract)
    """

    def __init__(self, llm: LLMBackend, workdir: Path | str = Path(".")):
        self.llm = llm
        self.workdir = Path(workdir)

    def run(
        self,
        change_intent: ChangeIntent,
        codebase_index: CodebaseIndex,
    ) -> ImpactSurface:
        """
        Analyze the blast radius of the proposed change.

        Steps:
        1. Identify the target function/class from change_intent.description
        2. Use jedi to find all call sites (blast radius)
        3. Use rope to validate the refactor is safe before proposing it
        4. Return an ImpactSurface describing everything at risk
        """
        surface = ImpactSurface()

        # 1. Find the target function name from the change intent
        target = self._extract_target(change_intent)
        if not target:
            log.warning("impact_analyst.no_target_found", intent=change_intent.description)
            return surface

        surface.primary_changed_symbol = target

        # 2. jedi: find all callers of the target function
        blast_result = self._find_callers(target)
        surface.callers = blast_result.callers
        surface.files_affected = blast_result.files_affected
        surface.total_callers = blast_result.total_callers

        # 3. jedi: find all functions in the target module
        module_functions = self._find_module_functions(target)
        surface.functions_at_risk.extend(module_functions)

        # 4. Classify risk level based on call graph
        surface.risk_level = self._classify_risk(
            total_callers=blast_result.total_callers,
            files_affected=len(blast_result.files_affected),
        )

        log.info(
            "impact_analyst.complete",
            target=target,
            callers=blast_result.total_callers,
            files=len(blast_result.files_affected),
            risk=surface.risk_level,
        )

        return surface

    def _extract_target(self, change_intent: ChangeIntent) -> str | None:
        """
        Extract the function/class name being changed from the intent description.

        Asks the LLM to identify the primary symbol being changed.
        """
        prompt = f"""Given this change intent, identify the primary function or class being changed.
Return ONLY the function or class name (e.g. "authenticate_user" or "UserService").

Change: {change_intent.description}

Rules:
- Return the exact function/method name
- Do NOT include parentheses or parameters
- Do NOT include the file path
- If multiple things are changing, return the most important one
"""
        try:
            response = self.llm.complete(
                prompt=prompt,
                system="You are a code analysis assistant. Output ONLY the symbol name, nothing else.",
                max_tokens=64,
                temperature=0.0,
            )
            name = (response.content or "").strip()
            # Sanitize
            name = name.split("\n")[0].strip()
            return name if name else None
        except Exception as e:
            log.warning("impact_analyst.llm_target_extraction_failed", error=str(e))
            return None

    def _find_callers(self, function_name: str) -> BlastRadiusResult:
        """
        Use jedi to find all call sites of function_name across the project.

        This is the key semantic operation: not regex, not text search —
        jedi resolves the actual function and finds every place it's called,
        including through aliases, imports, and methods.
        """
        from forge.code_intelligence.jedi_ import find_callers

        result = find_callers(workdir=self.workdir, function_name=function_name)

        callers = []
        files_affected: set[str] = set()

        for item in result.results:
            callers.append({
                "file": item.file,
                "line": item.line,
                "column": item.column,
                "caller_name": item.caller_name,
                "call_type": item.call_type,
            })
            if item.file:
                files_affected.add(item.file)

        return BlastRadiusResult(
            function_name=function_name,
            callers=callers,
            total_callers=len(callers),
            files_affected=list(files_affected),
        )

    def _find_module_functions(self, symbol_name: str) -> list[str]:
        """
        Find all functions and methods defined in the same module as symbol_name.

        These are "at risk" because any refactor of symbol_name may affect them.
        """
        from forge.code_intelligence.jedi_ import get_inference

        # Ask jedi for the module containing this symbol
        code = f"import sys\n{symbol_name}"
        result = get_inference(workdir=self.workdir, code=code)

        functions: list[str] = []
        for inf in result.results:
            if hasattr(inf, "full_name") and inf.full_name:
                functions.append(inf.full_name)

        return functions

    def _classify_risk(
        self,
        total_callers: int,
        files_affected: int,
    ) -> str:
        """Classify risk level based on blast radius."""
        if total_callers > 20 or files_affected > 5:
            return "HIGH"
        elif total_callers > 5 or files_affected > 2:
            return "MEDIUM"
        else:
            return "LOW"

    # ── Semantic Refactoring (used by Coder during edit pipeline) ─────────────

    def rename_symbol(
        self,
        old_name: str,
        new_name: str,
    ) -> SemanticRefactorResult:
        """
        Semantically rename a symbol across the entire project.

        Uses rope so all call sites, imports, and docstrings are updated correctly.
        """
        from forge.code_intelligence.rope_ import rename_function

        result = rename_function(
            workdir=self.workdir,
            old_name=old_name,
            new_name=new_name,
        )

        return SemanticRefactorResult(
            operation="rename",
            success=result.success,
            files_changed=result.files_changed,
            changes=[{"old": old_name, "new": new_name, "files": result.files_changed}],
            raw=result.raw,
        )

    def extract_method(
        self,
        source_path: str,
        name: str,
        start_line: int,
        end_line: int,
    ) -> SemanticRefactorResult:
        """
        Extract a range of lines into a new method.

        Uses rope to determine parameters and return value automatically.
        """
        from forge.code_intelligence.rope_ import extract_method

        result = extract_method(
            workdir=self.workdir,
            source_path=source_path,
            name=name,
            start_line=start_line,
            end_line=end_line,
        )

        return SemanticRefactorResult(
            operation="extract",
            success=result.success,
            files_changed=1 if result.success else 0,
            changes=[{"source": source_path, "extracted_as": name}],
            raw=result.raw,
        )
