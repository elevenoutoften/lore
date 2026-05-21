from __future__ import annotations

import ast
from pathlib import Path

from .schemas import RouteEntry

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def ingest_fastapi_routes(source_dir: str | Path) -> list[RouteEntry]:
    """Walk a source directory and extract FastAPI route decorators from .py files."""

    source_dir = Path(source_dir)
    routes: list[RouteEntry] = []

    if not source_dir.exists():
        return routes

    for py_file in source_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                route_info = _parse_route_decorator(decorator)
                if route_info is None:
                    continue
                method, path = route_info
                routes.append(
                    RouteEntry(
                        method=method,
                        path=path,
                        function_name=node.name,
                        file_path=str(py_file),
                        line_number=node.lineno,
                    )
                )
    return routes


def _parse_route_decorator(decorator: ast.expr) -> tuple[str, str] | None:
    """Extract (method, path) from a route decorator like @app.get('/path')."""

    if not isinstance(decorator, ast.Call):
        return None

    func = decorator.func
    if isinstance(func, ast.Attribute) and func.attr in METHODS:
        if decorator.args:
            arg = decorator.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return func.attr.upper(), arg.value

    if isinstance(func, ast.Attribute) and func.attr == "route":
        path = ""
        if decorator.args:
            arg = decorator.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                path = arg.value
        for kw in decorator.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        return elt.value.upper(), path
    return None

