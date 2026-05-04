"""
forge.product_compiler.contracts — Contract specifications for crosshair integration.

Defines the contract annotation format that crosshair can prove.
Crosshair supports PEP 316-style requires/ensures in docstrings, but the
enforce() API provides a programmatic way to register contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContractSpec:
    """
    A function's contract specification.

    Used by the Contract Writer to emit PEP 316 contracts that crosshair
    can prove. The coder generates these as docstring annotations in the
    generated code.

    Crosshair supports two contract formats:

    1. PEP 316 docstrings (requires/ensures):
        def fn(x):
            '''
            Requires:
                x > 0
            Ensures:
                result >= 0
            '''

    2. crosshair.enforce() wrapper:
        from crosshair import enforce
        @enforce
        def fn(x):
            requires(x > 0)
            ensures(result >= 0)

    We generate format 1 (docstring) since it doesn't require importing
    crosshair at runtime — only at proof time.
    """
    function_name: str
    file: str
    preconditions: list[str] = field(default_factory=list)   # e.g. ["x > 0", "email != ''"]
    postconditions: list[str] = field(default_factory=list)    # e.g. ["result >= 0", "isinstance(result, User)"]
    line: int = 0

    def to_docstring(self) -> str:
        """Render to a PEP 316 docstring block."""
        lines = ["    '''"]
        if self.preconditions:
            lines.append("    Requires:")
            for p in self.preconditions:
                lines.append(f"        {p}")
        if self.postconditions:
            lines.append("    Ensures:")
            for p in self.postconditions:
                lines.append(f"        {p}")
        lines.append("    '''")
        return "\n".join(lines)
