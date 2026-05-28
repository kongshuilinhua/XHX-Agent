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
    truncated: bool = False
    skipped_files: list[str] = Field(default_factory=list)
    max_references: int = 0
    max_references_per_symbol: int = 0
    max_lines_per_file: int = 0


DEFAULT_MAX_REFERENCES = 2_000
DEFAULT_MAX_REFERENCES_PER_SYMBOL = 40
DEFAULT_MAX_LINES_PER_FILE = 2_000


def build_reference_index(
    workspace: Path,
    repo_map: RepoMap | None = None,
    symbol_index: SymbolIndex | None = None,
    *,
    max_references: int = DEFAULT_MAX_REFERENCES,
    max_references_per_symbol: int = DEFAULT_MAX_REFERENCES_PER_SYMBOL,
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE,
) -> ReferenceIndex:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    symbol_index = symbol_index or build_symbol_index(root, repo_map)
    names = sorted({symbol.name for symbol in symbol_index.symbols if len(symbol.name) >= 3})
    if not names:
        return ReferenceIndex(
            root=str(root),
            max_references=max_references,
            max_references_per_symbol=max_references_per_symbol,
            max_lines_per_file=max_lines_per_file,
        )
    definitions = {(symbol.name, symbol.path, symbol.line) for symbol in symbol_index.symbols}
    patterns = {name: re.compile(rf"\b{re.escape(name)}\b") for name in names}
    references: list[Reference] = []
    per_symbol_counts: dict[str, int] = {}
    skipped_files: list[str] = []
    truncated = False
    for item in repo_map.files:
        if item.kind not in {"source", "test"}:
            continue
        if len(references) >= max_references:
            truncated = True
            break
        path = root / item.path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        if len(lines) > max_lines_per_file:
            skipped_files.append(item.path)
            truncated = True
            continue
        for line_number, line in enumerate(lines, start=1):
            for name, pattern in patterns.items():
                if (name, item.path, line_number) in definitions:
                    continue
                if per_symbol_counts.get(name, 0) >= max_references_per_symbol:
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
                    per_symbol_counts[name] = per_symbol_counts.get(name, 0) + 1
                    if len(references) >= max_references:
                        truncated = True
                        break
            if len(references) >= max_references:
                break
    return ReferenceIndex(
        root=str(root),
        references=sorted(references, key=lambda item: (item.name.lower(), item.path, item.line)),
        truncated=truncated,
        skipped_files=skipped_files,
        max_references=max_references,
        max_references_per_symbol=max_references_per_symbol,
        max_lines_per_file=max_lines_per_file,
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
