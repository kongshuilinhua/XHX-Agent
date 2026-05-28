from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from xhx_agent.repo_intel.symbols import Symbol, SymbolIndex, build_symbol_index, search_symbols


class SymbolContext(BaseModel):
    symbol: Symbol
    excerpt: str


def build_symbol_context(workspace: Path, query: str, *, limit: int = 5, context_lines: int = 3) -> list[SymbolContext]:
    index = build_symbol_index(workspace)
    return build_context_for_symbols(workspace, search_symbols(index, query, limit=limit), context_lines=context_lines)


def build_context_for_symbols(workspace: Path, symbols: list[Symbol], *, context_lines: int = 3) -> list[SymbolContext]:
    root = workspace.resolve()
    contexts: list[SymbolContext] = []
    for symbol in symbols:
        path = (root / symbol.path).resolve()
        if not _is_inside(root, path) or not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, symbol.line - context_lines)
        end_line = symbol.end_line or symbol.line
        end = min(len(lines), end_line + context_lines)
        excerpt = "\n".join(f"{line_no}: {lines[line_no - 1]}" for line_no in range(start, end + 1))
        contexts.append(SymbolContext(symbol=symbol, excerpt=excerpt))
    return contexts


def _is_inside(root: Path, path: Path) -> bool:
    return root == path or root in path.parents
