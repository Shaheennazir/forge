"""
forge.code_intelligence.parser — tree-sitter wrapper for multi-language AST parsing.

Supported languages: python, typescript, javascript, go, rust, c, cpp, java, ruby, bash.
Agents use this to understand code structure, find definitions, and analyze changes.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = structlog.get_logger(__name__)


# Lazy-load tree-sitter so the module loads even if tree-sitter isn't installed
_tree_sitter = None
_tslanguages = None


def _get_ts():
    global _tree_sitter, _tslanguages
    if _tree_sitter is None:
        import tree_sitter
        import tree_sitter_languages

        _tree_sitter = tree_sitter
        _tslanguages = tree_sitter_languages
    return _tree_sitter, _tslanguages


# Map file extensions to tree-sitter language names
_LANG_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
}


@dataclass
class ParsedFile:
    """A file parsed into an AST with extracted symbols."""

    path: str
    content: bytes
    language: str
    tree: any  # tree-sitter Tree — kept as opaque to avoid bindingversion issues
    root_node: any  # tree-sitter Node
    file_symbols: list[dict] = field(default_factory=list)  # top-level defs

    @property
    def source(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def find_definitions(self, name: str) -> list[tuple[int, int, int, int]]:
        """Find all definition sites of a symbol (line, col, end_line, end_col)."""
        return self._query(f"(function_definition name: (identifier) @name)")

    def find_references(self, name: str) -> list[tuple[int, int, int, int]]:
        """Find all references to a symbol."""
        return self._query(f'(identifier) @ref')

    def _query(self, query: str) -> list[tuple[int, int, int, int]]:
        """Run a tree-sitter query and return (start_line, start_col, end_line, end_col)."""
        ts, _ = _get_ts()
        try:
            q = ts.Query(self.root_node.language, query)
            caps = q.captures(self.root_node)
        except Exception:
            return []

        results = []
        for node, _ in caps:
            results.append((
                node.start_point[0], node.start_point[1],
                node.end_point[0], node.end_point[1],
            ))
        return results


class CodeParser:
    """
    Multi-language AST parser backed by tree-sitter.

    Usage:
        parser = CodeParser()
        pf = parser.parse_file("src/main.py")
        for sym in pf.file_symbols:
            print(sym)
    """

    _parsers: dict[str, any] = {}
    _languages: dict[str, any] = {}

    def __init__(self):
        self._cache: dict[str, ParsedFile] = {}

    def language_for_file(self, path: str) -> Optional[str]:
        """Return the tree-sitter language name for a file, or None if unsupported."""
        ext = Path(path).suffix.lower()
        return _LANG_MAP.get(ext)

    def language_for_extension(self, ext: str) -> Optional[str]:
        """Return the tree-sitter language name for an extension (include leading dot)."""
        return _LANG_MAP.get(ext.lower())

    def _get_parser(self, language: str) -> any:
        """Get or create a cached parser for a language."""
        ts, tsl = _get_ts()

        if language in self._parsers:
            return self._parsers[language]

        try:
            lang = tsl.get_language(language)
        except Exception as e:
            log.warning("codeintel.lang_unavailable", language=language, error=str(e))
            return None

        parser = ts.Parser()
        parser.set_language(lang)
        self._parsers[language] = parser
        self._languages[language] = lang
        return parser

    def parse_file(self, path: str | Path) -> Optional[ParsedFile]:
        """
        Parse a file and return a ParsedFile with AST and symbols.

        Caches by path — subsequent calls return the cached result.
        """
        path = str(path)
        if path in self._cache:
            return self._cache[path]

        p = Path(path)
        if not p.exists():
            return None

        try:
            content = p.read_bytes()
        except Exception:
            return None

        lang = self.language_for_file(path)
        if not lang:
            return None

        parser = self._get_parser(lang)
        if not parser:
            return None

        ts, _ = _get_ts()
        try:
            tree = parser.parse(content)
            root = tree.root_node
        except Exception as e:
            log.warning("codeintel.parse_failed", path=path, error=str(e))
            return None

        symbols = self._extract_symbols(root, lang)

        pf = ParsedFile(
            path=path,
            content=content,
            language=lang,
            tree=tree,
            root_node=root,
            file_symbols=symbols,
        )
        self._cache[path] = pf
        return pf

    def parse_content(self, content: str, language: str) -> Optional[ParsedFile]:
        """
        Parse raw string content as a given language.
        No caching — used for snippets, diffs, generated code.
        """
        ts, tsl = _get_ts()

        parser = self._get_parser(language)
        if not parser:
            return None

        try:
            tree = parser.parse(content.encode("utf-8"))
            root = tree.root_node
        except Exception as e:
            log.warning("codeintel.parse_content_failed", language=language, error=str(e))
            return None

        symbols = self._extract_symbols(root, language)

        return ParsedFile(
            path="<memory>",
            content=content.encode("utf-8"),
            language=language,
            tree=tree,
            root_node=root,
            file_symbols=symbols,
        )

    def _extract_symbols(self, root: any, language: str) -> list[dict]:
        """Walk the AST and collect top-level symbol definitions."""
        symbols = []
        for node in root.children:
            sym = self._node_to_symbol(node, language)
            if sym:
                symbols.append(sym)
        return symbols

    def _node_to_symbol(self, node: any, language: str) -> Optional[dict]:
        """Convert a top-level AST node to a symbol dict."""
        node_type = node.type

        # Python
        if language == "python":
            if node_type == "function_definition":
                name = self._get_child_text(node, "identifier") or "anonymous"
                return {"kind": "function", "name": name, "line": node.start_point[0] + 1}
            if node_type == "class_definition":
                name = self._get_child_text(node, "identifier") or "Anonymous"
                return {"kind": "class", "name": name, "line": node.start_point[0] + 1}
            if node_type in ("import_statement", "import_from_statement"):
                return {"kind": "import", "name": node.type, "line": node.start_point[0] + 1}

        # TypeScript / JavaScript
        if language in ("typescript", "javascript"):
            if node_type in ("function_declaration", "function"):
                name = self._get_func_name(node) or "anonymous"
                return {"kind": "function", "name": name, "line": node.start_point[0] + 1}
            if node_type == "class_declaration":
                name = self._get_child_text(node, "identifier") or "Anonymous"
                return {"kind": "class", "name": name, "line": node.start_point[0] + 1}

        # Go
        if language == "go":
            if node_type == "function_declaration":
                name = self._get_child_text(node, "identifier") or "main"
                return {"kind": "function", "name": name, "line": node.start_point[0] + 1}
            if node_type == "type_declaration":
                name = self._get_child_text(node, "type_identifier") or "T"
                return {"kind": "type", "name": name, "line": node.start_point[0] + 1}

        # Rust
        if language == "rust":
            if node_type in ("function_item", "function_declaration"):
                name = self._get_child_text(node, "identifier") or "?"
                return {"kind": "function", "name": name, "line": node.start_point[0] + 1}
            if node_type == "struct_item":
                name = self._get_child_text(node, "type_identifier") or "?"
                return {"kind": "struct", "name": name, "line": node.start_point[0] + 1}

        return None

    def _get_child_text(self, node: any, child_type: str) -> Optional[str]:
        """Get the text of the first direct child of a given type."""
        for child in node.children:
            if child.type == child_type:
                txt = child.text.decode("utf-8", errors="replace") if hasattr(child, "text") else str(child)
                return txt
        return None

    def _get_func_name(self, node: any) -> Optional[str]:
        """Get function name, handling the different TypeScript node shapes."""
        for child in node.children:
            if child.type == "identifier":
                return bytes(child).decode("utf-8", errors="replace")
        return None

    def clear_cache(self) -> None:
        """Clear the per-path parse cache."""
        self._cache.clear()
