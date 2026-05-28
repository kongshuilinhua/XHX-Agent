from __future__ import annotations

import ast
import re
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map


class Symbol(BaseModel):
    name: str
    kind: str
    path: str
    line: int
    end_line: int | None = None
    language: str
    parent: str | None = None


class SymbolIndex(BaseModel):
    root: str
    symbols: list[Symbol] = Field(default_factory=list)


def build_symbol_index(workspace: Path, repo_map: RepoMap | None = None) -> SymbolIndex:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    symbols: list[Symbol] = []
    for item in repo_map.files:
        if item.kind not in {"source", "test"}:
            continue
        path = root / item.path
        if item.language == "python":
            symbols.extend(_python_symbols(root, path))
        elif item.language in {"javascript", "typescript"}:
            symbols.extend(_js_ts_symbols(root, path, item.language))
    return SymbolIndex(root=str(root), symbols=sorted(symbols, key=lambda item: (item.path, item.line, item.name)))


def search_symbols(index: SymbolIndex, query: str, *, limit: int = 20) -> list[Symbol]:
    lowered = query.lower()
    if not lowered:
        return index.symbols[:limit]
    exact = [symbol for symbol in index.symbols if symbol.name.lower() == lowered]
    prefix = [symbol for symbol in index.symbols if symbol not in exact and symbol.name.lower().startswith(lowered)]
    contains = [symbol for symbol in index.symbols if symbol not in exact and symbol not in prefix and lowered in symbol.name.lower()]
    return (exact + prefix + contains)[:limit]


def _python_symbols(root: Path, path: Path) -> list[Symbol]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    relative = path.relative_to(root).as_posix()
    symbols: list[Symbol] = []

    def visit(node: ast.AST, parent: str | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind="class",
                        path=relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", None),
                        language="python",
                        parent=parent,
                    )
                )
                visit(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind="function",
                        path=relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", None),
                        language="python",
                        parent=parent,
                    )
                )
                visit(child, child.name if parent is None else f"{parent}.{child.name}")
            else:
                visit(child, parent)

    visit(tree)
    return symbols


_JS_TS_PATTERNS = [
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?[^=]*=>")),
]


def _js_ts_symbols(root: Path, path: Path, language: str) -> list[Symbol]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    relative = path.relative_to(root).as_posix()
    symbols: list[Symbol] = []
    for index, line in enumerate(lines, start=1):
        for kind, pattern in _JS_TS_PATTERNS:
            match = pattern.match(line)
            if match:
                symbols.append(
                    Symbol(
                        name=match.group(1),
                        kind=kind,
                        path=relative,
                        line=index,
                        language=language,
                    )
                )
                break
    return symbols
