from __future__ import annotations

import ast
from pathlib import Path

from .schemas import SymbolEntry


def ingest_python_symbols(source_dir: str | Path) -> list[SymbolEntry]:
    """Extract function and class symbols from Python source files."""

    source_dir = Path(source_dir)
    symbols: list[SymbolEntry] = []

    if not source_dir.exists():
        return symbols

    resolved_source_dir = source_dir.resolve()
    for py_file in source_dir.rglob("*.py"):
        try:
            resolved_file = py_file.resolve()
        except (OSError, RuntimeError):
            continue
        if not resolved_file.is_relative_to(resolved_source_dir):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if _is_method(node, tree) else "function"
                symbols.append(
                    SymbolEntry(
                        name=node.name,
                        kind=kind,
                        file_path=str(py_file),
                        line_number=node.lineno,
                        docstring=ast.get_docstring(node),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                methods = [
                    child.name
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_")
                ]
                symbols.append(
                    SymbolEntry(
                        name=node.name,
                        kind="class",
                        file_path=str(py_file),
                        line_number=node.lineno,
                        docstring=ast.get_docstring(node),
                        exports=methods,
                    )
                )
    return symbols


def _is_method(node: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.AST) -> bool:
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef) and node in parent.body:
            return True
    return False
