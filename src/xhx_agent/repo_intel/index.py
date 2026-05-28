from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from xhx_agent.repo_intel.imports import ImportGraph, build_import_graph
from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map
from xhx_agent.repo_intel.symbols import SymbolIndex, build_symbol_index


class RepoIntelIndex(BaseModel):
    schema_version: int = 1
    created_at: str
    repo_map: RepoMap
    symbol_index: SymbolIndex
    import_graph: ImportGraph


def build_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    root = workspace.resolve()
    repo_map = build_repo_map(root)
    symbol_index = build_symbol_index(root, repo_map)
    import_graph = build_import_graph(root, repo_map)
    return RepoIntelIndex(
        created_at=datetime.now(UTC).isoformat(),
        repo_map=repo_map,
        symbol_index=symbol_index,
        import_graph=import_graph,
    )


def repo_index_path(workspace: Path) -> Path:
    return workspace / ".xhx" / "repo" / "index.json"


def write_repo_intel_index(workspace: Path, index: RepoIntelIndex | None = None) -> Path:
    path = repo_index_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    index = index or build_repo_intel_index(workspace)
    path.write_text(index.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    return RepoIntelIndex.model_validate_json(repo_index_path(workspace).read_text(encoding="utf-8"))


def load_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    try:
        return read_repo_intel_index(workspace)
    except (OSError, ValueError):
        return build_repo_intel_index(workspace)
