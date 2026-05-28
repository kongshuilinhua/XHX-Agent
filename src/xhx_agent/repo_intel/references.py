from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map
from xhx_agent.repo_intel.symbols import SymbolIndex, build_symbol_index


class Reference(BaseModel):
    name: str
    path: str
    line: int
    excerpt: str


class ReferenceIndex(BaseModel):
    root: str
    references: list[Reference] = Field(default_factory=list)


def build_reference_index(
    workspace: Path,
    repo_map: RepoMap | None = None,
    symbol_index: SymbolIndex | None = None,
) -> ReferenceIndex:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    symbol_index = symbol_index or build_symbol_index(root, repo_map)
    names = sorted({symbol.name for symbol in symbol_index.symbols if len(symbol.name) >= 3})
    if not names:
        return ReferenceIndex(root=str(root))
    definitions = {(symbol.name, symbol.path, symbol.line) for symbol in symbol_index.symbols}
    patterns = {name: re.compile(rf"\b{re.escape(name)}\b") for name in names}
    references: list[Reference] = []
    for item in repo_map.files:
        if item.kind not in {"source", "test"}:
            continue
        path = root / item.path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for name, pattern in patterns.items():
                if (name, item.path, line_number) in definitions:
                    continue
                if pattern.search(line):
                    references.append(
                        Reference(
                            name=name,
                            path=item.path,
                            line=line_number,
                            excerpt=line.strip(),
                        )
                    )
    return ReferenceIndex(
        root=str(root),
        references=sorted(references, key=lambda item: (item.name.lower(), item.path, item.line)),
    )


def search_references(index: ReferenceIndex, query: str, *, limit: int = 20) -> list[Reference]:
    lowered = query.lower()
    if not lowered:
        return index.references[:limit]
    exact = [reference for reference in index.references if reference.name.lower() == lowered]
    prefix = [
        reference
        for reference in index.references
        if reference not in exact and reference.name.lower().startswith(lowered)
    ]
    contains = [
        reference
        for reference in index.references
        if reference not in exact and reference not in prefix and lowered in reference.name.lower()
    ]
    return (exact + prefix + contains)[:limit]
