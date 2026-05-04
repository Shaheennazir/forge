"""
forge.product_compiler.codebase_index — Index and query the current codebase state.

Provides the indexed view of the codebase that the edit pipeline uses to
determine blast radius, extract rules, and validate changes.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Supporting data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ImportEdge:
    """A single import dependency between two files."""
    from_file: str
    to_module: str  # dotted module path or file path


@dataclass
class CallEdge:
    """A function call from one symbol to another."""
    caller_file: str
    caller_symbol: str  # fully-qualified name
    callee_symbol: str  # fully-qualified name


@dataclass
class SymbolInfo:
    """Metadata about a discovered symbol."""
    name: str
    kind: str  # "function" | "class" | "async_function"
    file_path: str
    lineno: int
    qualified_name: str  # module.Class.method or module.function


# ─────────────────────────────────────────────────────────────────────────────
# CodebaseIndex
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CodebaseIndex:
    """
    Indexed representation of the codebase for the edit pipeline.

    Built once at the start of an edit session and updated incrementally
    after each code change. Used by ImpactAnalystAgent to resolve the
    blast radius of a proposed change.

    Attributes
    ----------
    symbols_by_file : dict[str, list[str]]
        file_path → list of top-level symbols (function/def/class names)

    source_by_file : dict[str, str]
        file_path → full source text

    symbol_location : dict[str, str]
        fully-qualified function/class name → file path

    dependency_graph : dict[str, list[str]]
        file_path → list of files it depends on (imports), resolved as file paths

    call_graph : dict[str, list[str]]
        fully-qualified caller → list of fully-qualified callees

    contract_index : dict[str, dict]
        symbol qualified name → {"file", "lineno", "docstring", "decorators"}
        Decorators tagged with @contract are treated as API contracts.

    test_coverage : dict[str, list[str]]
        file_path (or symbol) → list of test names that exercise it
    """

    # Existing fields
    symbols_by_file: dict[str, list[str]] = field(default_factory=dict)
    source_by_file: dict[str, str] = field(default_factory=dict)
    symbol_location: dict[str, str] = field(default_factory=dict)

    # New: dependency graph (file → files it imports)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)

    # New: call graph (qualified name → qualified names called)
    call_graph: dict[str, list[str]] = field(default_factory=dict)

    # New: contract index (qualified name → contract metadata)
    contract_index: dict[str, dict] = field(default_factory=dict)

    # New: test coverage map (source file → test names)
    test_coverage: dict[str, list[str]] = field(default_factory=dict)

    # Root of the indexed project (set during build)
    _project_root: Optional[str] = field(default=None, repr=False)

    # All discovered symbols
    _all_symbols: dict[str, SymbolInfo] = field(default_factory=dict, repr=False)

    # All import edges seen during parse
    _import_edges: list[ImportEdge] = field(default_factory=list, repr=False)

    # All call edges seen during parse
    _call_edges: list[CallEdge] = field(default_factory=list, repr=False)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def build(self, project_root: str | None = None) -> None:
        """
        Scan the project directory and populate all index fields.

        Uses Python's `ast` module to parse every .py file under the project
        root and build:
          - symbols_by_file / symbol_location
          - dependency_graph  (import-based)
          - call_graph        (Call AST nodes)
          - contract_index    (functions/classes with docstrings or decorators)
          - test_coverage     (test files referencing source symbols)

        Parameters
        ----------
        project_root : str, optional
            Path to the project root. Defaults to the directory containing
            this file's package (two levels up from this module).
        """
        if project_root is None:
            # Infer from this file's location: .../forge/src/forge/product_compiler
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )

        self._project_root = os.path.abspath(project_root)
        self._discover_files(self._project_root)
        # Resolve import edges into a file-level dependency graph
        self.resolve_dependency_graph()

    def update_after_change(self, changed_files: list[str]) -> None:
        """
        Incrementally update the index after a set of files were modified.

        Re-parses only the changed files and updates all affected index
        structures in-place.

        Parameters
        ----------
        changed_files : List of file paths that were just changed.
        """
        for file_path in changed_files:
            abs_path = os.path.abspath(file_path)

            # Remove old entries for this file
            self._remove_file_entries(abs_path)

            # Re-parse and re-index
            if os.path.exists(abs_path) and abs_path.endswith(".py"):
                self._parse_and_index_file(abs_path)

    def query_impact(self, change_description: str) -> list[str]:
        """
        Return the list of files likely affected by the described change.

        Uses keyword matching against symbol names and file paths to produce
        a conservative (over-inclusive) list of files in scope.

        Parameters
        ----------
        change_description : Plain-language description of the proposed change.

        Returns
        -------
        List of file paths in scope for the change.
        """
        keywords = change_description.lower().split()
        affected: set[str] = set()

        # Match keywords against symbol names
        for sym_name, info in self._all_symbols.items():
            sym_lower = sym_name.lower()
            if any(kw in sym_lower for kw in keywords):
                affected.add(info.file_path)

        # Match keywords against file paths
        for file_path in self.symbols_by_file:
            file_lower = file_path.lower()
            if any(kw in file_lower for kw in keywords):
                affected.add(file_path)

        # Always include test files when keywords like "test" appear
        if any(kw in ("test", "tests", "testing") for kw in keywords):
            for file_path in self.symbols_by_file:
                if "_test" in file_path or file_path.endswith("_test.py"):
                    affected.add(file_path)

        return sorted(affected)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _discover_files(self, root: str) -> None:
        """Walk the project root and parse every .py file."""
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip hidden, cache, and non-Python dirs
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                and d not in ("__pycache__", "node_modules", "venv", ".venv")
            ]

            for filename in sorted(filenames):
                if filename.endswith(".py"):
                    file_path = os.path.join(dirpath, filename)
                    self._parse_and_index_file(file_path)

    def _parse_and_index_file(self, file_path: str) -> None:
        """Parse a single Python file and populate all index structures."""
        try:
            source = self._read_source(file_path)
        except (OSError, UnicodeDecodeError):
            return

        self.source_by_file[file_path] = source

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return

        module_name = self._module_name_from_path(file_path)
        visitor = _SymbolVisitor(module_name, file_path)
        visitor.visit(tree)

        # Populate symbols_by_file
        file_symbols = [s.qualified_name for s in visitor.symbols]
        self.symbols_by_file[file_path] = file_symbols

        # Populate symbol_location and _all_symbols
        for sym in visitor.symbols:
            self.symbol_location[sym.qualified_name] = file_path
            self._all_symbols[sym.qualified_name] = sym

        # Populate call_graph edges
        for edge in visitor.call_edges:
            caller = edge.caller_symbol
            if caller not in self.call_graph:
                self.call_graph[caller] = []
            self.call_graph[caller].append(edge.callee_symbol)

        # Collect import edges for dependency_graph resolution
        self._import_edges.extend(visitor.import_edges)

        # Populate contract_index (symbols with docstrings or @contract decorator)
        for sym in visitor.symbols:
            if sym.qualified_name in visitor.docstrings or \
               sym.qualified_name in visitor.decorated_names:
                self.contract_index[sym.qualified_name] = {
                    "file": file_path,
                    "lineno": sym.lineno,
                    "docstring": visitor.docstrings.get(sym.qualified_name, ""),
                    "decorators": visitor.decorated_names.get(sym.qualified_name, []),
                }

        # Test coverage: test files reference source symbols via calls or imports
        if self._is_test_file(file_path):
            for called_sym in visitor.calls_outside_module:
                self._record_test_coverage(called_sym, visitor.test_name, file_path)

    def _read_source(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read()

    def _module_name_from_path(self, file_path: str) -> str:
        """Derive a dotted module name from a file path, relative to project root."""
        if self._project_root and file_path.startswith(self._project_root):
            rel = file_path[len(self._project_root):].lstrip(os.sep)
        else:
            rel = os.path.basename(file_path)

        # Replace __init__.py strip, .py extension, directory separators
        if rel.endswith("__init__.py"):
            rel = rel[:-12]
        elif rel.endswith(".py"):
            rel = rel[:-3]
        return rel.replace(os.sep, ".").replace("/", ".")

    def _is_test_file(self, file_path: str) -> bool:
        basename = os.path.basename(file_path)
        return (
            basename.startswith("test_")
            or basename.endswith("_test.py")
            or basename.startswith("Test")
            or "_test" in file_path
        )

    def _record_test_coverage(self, called_sym: str, test_name: str, test_file: str) -> None:
        """Record that a test covers a given symbol (by qualified name or file)."""
        # Cover by qualified symbol name
        if called_sym not in self.test_coverage:
            self.test_coverage[called_sym] = []
        if test_name not in self.test_coverage[called_sym]:
            self.test_coverage[called_sym].append(test_name)

        # Also cover by file
        if called_sym in self.symbol_location:
            src_file = self.symbol_location[called_sym]
            if src_file not in self.test_coverage:
                self.test_coverage[src_file] = []
            if test_name not in self.test_coverage[src_file]:
                self.test_coverage[src_file].append(test_name)

    def _remove_file_entries(self, file_path: str) -> None:
        """Remove all index entries associated with a given file."""
        # Remove from symbols_by_file
        self.symbols_by_file.pop(file_path, None)

        # Remove from source_by_file
        self.source_by_file.pop(file_path, None)

        # Remove symbol_location entries pointing to this file
        dead_keys = [k for k, v in self.symbol_location.items() if v == file_path]
        for k in dead_keys:
            del self.symbol_location[k]

        # Remove from _all_symbols
        dead_syms = [k for k, v in self._all_symbols.items() if v.file_path == file_path]
        for k in dead_syms:
            del self._all_symbols[k]

        # Remove call graph entries from callers in this file
        dead_callers = [
            k for k in self.call_graph
            if self._qualified_file(k) == file_path
        ]
        for k in dead_callers:
            del self.call_graph[k]

        # Remove call graph entries pointing to symbols in this file
        for caller, callees in self.call_graph.items():
            self.call_graph[caller] = [c for c in callees if self._qualified_file(c) != file_path]

        # Remove from contract_index
        dead_contracts = [
            k for k, v in self.contract_index.items()
            if v.get("file") == file_path
        ]
        for k in dead_contracts:
            del self.contract_index[k]

        # Remove from test_coverage
        self.test_coverage.pop(file_path, None)

        # Remove import edges from this file
        self._import_edges = [
            e for e in self._import_edges if e.from_file != file_path
        ]

    def _qualified_file(self, qualified_name: str) -> str:
        """Return the file path for a qualified symbol name, or empty string."""
        return self.symbol_location.get(qualified_name, "")

    # ─────────────────────────────────────────────────────────────────────────
    # Dependency graph resolver
    # ─────────────────────────────────────────────────────────────────────────

    def resolve_dependency_graph(self) -> dict[str, list[str]]:
        """
        Compute a resolved dependency graph (file → list of file paths it imports).

        Returns ``dependency_graph`` with module paths resolved to file paths
        using the indexed ``symbol_location`` map.
        """
        resolved: dict[str, list[str]] = {f: [] for f in self.symbols_by_file}

        for edge in self._import_edges:
            # Try to resolve module → file path
            target_file = self._resolve_module_to_file(edge.to_module)
            if target_file and target_file in self.symbols_by_file:
                if edge.from_file in resolved:
                    resolved[edge.from_file].append(target_file)

        # Merge with previously built dependency_graph
        for f, deps in resolved.items():
            if f not in self.dependency_graph:
                self.dependency_graph[f] = []
            for d in deps:
                if d not in self.dependency_graph[f]:
                    self.dependency_graph[f].append(d)

        return self.dependency_graph

    def _resolve_module_to_file(self, module: str) -> str | None:
        """
        Resolve a dotted module path to a file path.

        Strategy:
        1. Build a basename → file path map for all indexed files
           (handles ``forge.product_compiler.models`` by matching ``models``).
        2. Also check prefix matching for packages
           (handles ``forge.product_compiler`` → ``.../forge/product_compiler/__init__.py``).
        3. Return the first matching file that exists on disk.
        """
        if not self._project_root:
            return None

        # Index: basename → file path for fast lookup
        # Strip .py and __init__.py to get the module name
        basename_map: dict[str, str] = {}
        for file_path in self.symbols_by_file:
            base = os.path.basename(file_path)
            base = base.replace("__init__.py", "")
            base = base.replace(".py", "")
            if base:
                basename_map[base] = file_path

        # Try exact full-path prefix match (e.g. forge.product_compiler.models)
        for qualified_name, file_path in self.symbol_location.items():
            if qualified_name == module or qualified_name.startswith(module + "."):
                if os.path.isfile(file_path):
                    return file_path

        # Try by last segment of the module (e.g. models → .../models.py)
        parts = module.split(".")
        for i in range(len(parts)):
            partial = ".".join(parts[i:])
            if partial in basename_map:
                candidate = basename_map[partial]
                if os.path.isfile(candidate):
                    return candidate

        # Try matching the last segment only
        last = parts[-1]
        if last in basename_map:
            candidate = basename_map[last]
            if os.path.isfile(candidate):
                return candidate

        return None


# ─────────────────────────────────────────────────────────────────────────────
# AST Visitor
# ─────────────────────────────────────────────────────────────────────────────


class _SymbolVisitor(ast.NodeVisitor):
    """
    Walks an AST and collects:
    - Top-level symbols (functions, classes, async functions)
    - All import statements
    - All Call nodes (for call graph)
    - Docstrings per qualified name
    - Decorators per qualified name
    """

    __slots__ = (
        "module_name", "file_path", "symbols", "import_edges",
        "call_edges", "docstrings", "decorated_names",
        "calls_outside_module", "_in_function", "_class_stack",
        "_current_function", "_current_class", "test_name",
    )

    def __init__(self, module_name: str, file_path: str):
        self.module_name = module_name
        self.file_path = file_path
        self.symbols: list[SymbolInfo] = []
        self.import_edges: list[ImportEdge] = []
        self.call_edges: list[CallEdge] = []
        self.docstrings: dict[str, str] = {}
        self.decorated_names: dict[str, list[str]] = {}
        self.calls_outside_module: list[str] = []
        self._in_function = False
        self._class_stack: list[str] = []  # stack of enclosing class names
        self._current_function: str | None = None
        self.test_name: str = ""

    # ─── Import handling ─────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.import_edges.append(ImportEdge(
                from_file=self.file_path,
                to_module=alias.name,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.import_edges.append(ImportEdge(
                from_file=self.file_path,
                to_module=node.module,
            ))
        self.generic_visit(node)

    # ─── Symbol handling (top-level only) ───────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "async_function")

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        if self._class_stack:
            # Method inside a class
            qualified = f"{'.'.join(self._class_stack)}.{node.name}"
        else:
            qualified = f"{self.module_name}.{node.name}"

        docstring = ast.get_docstring(node) or ""
        if docstring:
            self.docstrings[qualified] = docstring

        decorators = [self._dotted_name(d) for d in node.decorator_list]
        if decorators:
            self.decorated_names[qualified] = decorators

        self.symbols.append(SymbolInfo(
            name=node.name,
            kind=kind,
            file_path=self.file_path,
            lineno=node.lineno or 0,
            qualified_name=qualified,
        ))

        # Infer test name from function if this is a test file
        if self._is_test_function_name(node.name):
            self.test_name = node.name

        # Descend into function body to collect nested calls
        old_in_function = self._in_function
        old_current = self._current_function
        self._in_function = True
        self._current_function = qualified
        self.generic_visit(node)
        self._in_function = old_in_function
        self._current_function = old_current

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = f"{self.module_name}.{node.name}"

        docstring = ast.get_docstring(node) or ""
        if docstring:
            self.docstrings[qualified] = docstring

        decorators = [self._dotted_name(d) for d in node.decorator_list]
        if decorators:
            self.decorated_names[qualified] = decorators

        self.symbols.append(SymbolInfo(
            name=node.name,
            kind="class",
            file_path=self.file_path,
            lineno=node.lineno or 0,
            qualified_name=qualified,
        ))

        self._class_stack.append(qualified)
        self.generic_visit(node)
        self._class_stack.pop()

    # ─── Call handling ───────────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        if self._current_function:
            callee = self._resolve_callee(node)
            if callee:
                self.call_edges.append(CallEdge(
                    caller_file=self.file_path,
                    caller_symbol=self._current_function,
                    callee_symbol=callee,
                ))
                if not callee.startswith(self.module_name):
                    self.calls_outside_module.append(callee)
        self.generic_visit(node)

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _dotted_name(self, node: ast.expr) -> str:
        """Return dotted name for an ast.Name or ast.Attribute chain."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            base = self._dotted_name(node.value)
            return f"{base}.{node.attr}"
        elif isinstance(node, ast.Constant):
            return str(node.value)
        return ""

    def _resolve_callee(self, node: ast.Call) -> str | None:
        """Resolve a Call node's func to a qualified name string."""
        if isinstance(node.func, ast.Name):
            # Local or imported function name
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            return self._dotted_name(node.func)
        return None

    def _is_test_function_name(self, name: str) -> bool:
        return (
            name.startswith("test_")
            or name.startswith("Test")
            or name.endswith("_test")
            or name.endswith("_tests")
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mock data support (when AST alone is insufficient)
# ─────────────────────────────────────────────────────────────────────────────


def build_mock_index(project_root: str | None = None) -> CodebaseIndex:
    """
    Return a CodebaseIndex populated with representative mock data.

    Used as a fallback when the real AST-based build() cannot run,
    or for testing the index query interface without a real codebase.
    """
    index = CodebaseIndex()

    mock_source = '''
"""Example module for testing."""

def get_user(user_id: int) -> dict:
    """Fetch a user by ID."""
    return {"id": user_id, "name": "Alice"}

async def create_user(name: str, email: str) -> dict:
    """Create a new user."""
    return {"id": 1, "name": name, "email": email}

class UserService:
    """User management service."""

    def list_users(self) -> list[dict]:
        return [get_user(1)]

    async def delete_user(self, user_id: int) -> None:
        pass
'''

    index.source_by_file["src/example.py"] = mock_source
    index.symbols_by_file["src/example.py"] = [
        "example.get_user",
        "example.create_user",
        "example.UserService",
    ]

    index.symbol_location["example.get_user"] = "src/example.py"
    index.symbol_location["example.create_user"] = "src/example.py"
    index.symbol_location["example.UserService"] = "src/example.py"
    index.symbol_location["example.UserService.list_users"] = "src/example.py"
    index.symbol_location["example.UserService.delete_user"] = "src/example.py"

    index.call_graph["example.get_user"] = []
    index.call_graph["example.UserService.list_users"] = ["example.get_user"]

    index.contract_index["example.get_user"] = {
        "file": "src/example.py",
        "lineno": 4,
        "docstring": "Fetch a user by ID.",
        "decorators": [],
    }
    index.contract_index["example.create_user"] = {
        "file": "src/example.py",
        "lineno": 9,
        "docstring": "Create a new user.",
        "decorators": [],
    }

    index.test_coverage["src/example.py"] = ["test_get_user", "test_create_user"]

    return index
