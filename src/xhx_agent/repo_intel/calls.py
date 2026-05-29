from __future__ import annotations

import ast
import re
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map
from xhx_agent.repo_intel.symbols import Symbol, SymbolIndex, build_symbol_index


class CallEdge(BaseModel):
    caller: str
    caller_path: str
    caller_line: int
    callee: str
    callee_path: str | None = None
    callee_line: int | None = None
    call_line: int
    language: str
    confidence: float = 0.0


class CallGraph(BaseModel):
    root: str
    edges: list[CallEdge] = Field(default_factory=list)
    truncated: bool = False
    max_edges: int = 0


DEFAULT_MAX_CALL_EDGES = 2_000


def build_call_graph(
    workspace: Path,
    repo_map: RepoMap | None = None,
    symbol_index: SymbolIndex | None = None,
    *,
    max_edges: int = DEFAULT_MAX_CALL_EDGES,
) -> CallGraph:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    symbol_index = symbol_index or build_symbol_index(root, repo_map)
    resolver = _SymbolResolver(symbol_index.symbols)
    edges: list[CallEdge] = []
    truncated = False
    for item in repo_map.files:
        if item.kind not in {"source", "test"}:
            continue
        path = root / item.path
        if item.language == "python":
            edges.extend(_python_call_edges(root, path, resolver))
        elif item.language in {"javascript", "typescript"}:
            file_symbols = [symbol for symbol in symbol_index.symbols if symbol.path == item.path]
            edges.extend(_js_ts_call_edges(root, path, item.language, file_symbols, resolver))
        if len(edges) >= max_edges:
            edges = edges[:max_edges]
            truncated = True
            break
    return CallGraph(
        root=str(root),
        edges=sorted(edges, key=lambda edge: (edge.caller_path, edge.caller_line, edge.call_line, edge.callee)),
        truncated=truncated,
        max_edges=max_edges,
    )


class _SymbolResolver:
    def __init__(self, symbols: list[Symbol]) -> None:
        self._symbols = symbols

    def resolve(self, name: str, path: str) -> tuple[Symbol | None, float]:
        same_file = [symbol for symbol in self._symbols if symbol.name == name and symbol.path == path]
        if len(same_file) == 1:
            return same_file[0], 0.80
        project = [symbol for symbol in self._symbols if symbol.name == name]
        if len(project) == 1:
            return project[0], 0.60
        return None, 0.35 if project else 0.20


def _python_call_edges(root: Path, path: Path, resolver: _SymbolResolver) -> list[CallEdge]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    relative = path.relative_to(root).as_posix()
    edges: list[CallEdge] = []

    def visit(node: ast.AST, parent: str | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                caller = child.name if parent is None else f"{parent}.{child.name}"
                for call in [item for item in ast.walk(child) if isinstance(item, ast.Call)]:
                    name = _python_call_name(call.func)
                    if not name or name == child.name:
                        continue
                    target, confidence = resolver.resolve(name, relative)
                    edges.append(
                        CallEdge(
                            caller=caller,
                            caller_path=relative,
                            caller_line=child.lineno,
                            callee=name,
                            callee_path=target.path if target else None,
                            callee_line=target.line if target else None,
                            call_line=getattr(call, "lineno", child.lineno),
                            language="python",
                            confidence=confidence,
                        )
                    )
                visit(child, caller)

    visit(tree)
    return edges


def _python_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


_JS_TS_CALL_PATTERN = re.compile(r"(?:\.\s*)?([A-Za-z_$][\w$]*)\s*\(")
_JS_TS_CALL_STOPWORDS = {
    "catch",
    "for",
    "function",
    "if",
    "return",
    "switch",
    "while",
}


def _js_ts_call_edges(
    root: Path,
    path: Path,
    language: str,
    symbols: list[Symbol],
    resolver: _SymbolResolver,
) -> list[CallEdge]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    relative = path.relative_to(root).as_posix()
    ordered_symbols = sorted(symbols, key=lambda symbol: symbol.line)
    edges: list[CallEdge] = []
    for index, symbol in enumerate(ordered_symbols):
        end_line = ordered_symbols[index + 1].line - 1 if index + 1 < len(ordered_symbols) else len(lines)
        for line_number in range(symbol.line, end_line + 1):
            line = lines[line_number - 1]
            for match in _JS_TS_CALL_PATTERN.finditer(line):
                name = match.group(1)
                if name in _JS_TS_CALL_STOPWORDS or name == symbol.name:
                    continue
                target, confidence = resolver.resolve(name, relative)
                edges.append(
                    CallEdge(
                        caller=symbol.name,
                        caller_path=relative,
                        caller_line=symbol.line,
                        callee=name,
                        callee_path=target.path if target else None,
                        callee_line=target.line if target else None,
                        call_line=line_number,
                        language=language,
                        confidence=confidence,
                    )
                )
    return edges
