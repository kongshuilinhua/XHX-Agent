from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.imports import ImportGraph, build_import_graph
from xhx_agent.repo_intel.references import ReferenceIndex, build_reference_index
from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map
from xhx_agent.repo_intel.symbols import SymbolIndex, build_symbol_index


class RepoIntelIndex(BaseModel):
    schema_version: int = 1
    created_at: str
    content_fingerprint: str
    repo_map: RepoMap
    symbol_index: SymbolIndex
    import_graph: ImportGraph
    reference_index: ReferenceIndex = Field(default_factory=lambda: ReferenceIndex(root=""))


class RepoIntelDiagnostics(BaseModel):
    path: str
    status: str
    schema_version: int | None = None
    size_bytes: int = 0
    file_count: int = 0
    symbol_count: int = 0
    import_edge_count: int = 0
    reference_count: int = 0
    reference_truncated: bool = False
    skipped_reference_files: list[str] = Field(default_factory=list)
    content_fingerprint: str | None = None
    current_fingerprint: str | None = None
    reason: str = ""


def build_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    root = workspace.resolve()
    repo_map = build_repo_map(root)
    symbol_index = build_symbol_index(root, repo_map)
    import_graph = build_import_graph(root, repo_map)
    reference_index = build_reference_index(root, repo_map, symbol_index)
    return RepoIntelIndex(
        created_at=datetime.now(UTC).isoformat(),
        content_fingerprint=repo_map_fingerprint(repo_map),
        repo_map=repo_map,
        symbol_index=symbol_index,
        import_graph=import_graph,
        reference_index=reference_index,
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
        index = read_repo_intel_index(workspace)
    except (OSError, ValueError):
        return build_repo_intel_index(workspace)
    current_repo_map = build_repo_map(workspace)
    if index.content_fingerprint != repo_map_fingerprint(current_repo_map):
        return build_repo_intel_index(workspace)
    if not index.reference_index.root:
        return build_repo_intel_index(workspace)
    return index


def diagnose_repo_intel_index(workspace: Path) -> RepoIntelDiagnostics:
    path = repo_index_path(workspace)
    if not path.exists():
        return RepoIntelDiagnostics(path=str(path), status="missing", reason="Repo intelligence index does not exist.")
    size_bytes = path.stat().st_size
    try:
        index = read_repo_intel_index(workspace)
    except (OSError, ValueError) as exc:
        return RepoIntelDiagnostics(
            path=str(path),
            status="invalid",
            size_bytes=size_bytes,
            reason=f"Repo intelligence index could not be parsed: {exc}",
        )
    current_repo_map = build_repo_map(workspace)
    current_fingerprint = repo_map_fingerprint(current_repo_map)
    status = "current"
    reason = "Repo intelligence index is current."
    if index.content_fingerprint != current_fingerprint:
        status = "stale"
        reason = "Repo intelligence index fingerprint does not match current files."
    elif not index.reference_index.root:
        status = "stale"
        reason = "Repo intelligence index is missing reference_index and should be rebuilt."
    return RepoIntelDiagnostics(
        path=str(path),
        status=status,
        schema_version=index.schema_version,
        size_bytes=size_bytes,
        file_count=len(index.repo_map.files),
        symbol_count=len(index.symbol_index.symbols),
        import_edge_count=len(index.import_graph.edges),
        reference_count=len(index.reference_index.references),
        reference_truncated=index.reference_index.truncated,
        skipped_reference_files=index.reference_index.skipped_files,
        content_fingerprint=index.content_fingerprint,
        current_fingerprint=current_fingerprint,
        reason=reason,
    )


def repo_map_fingerprint(repo_map: RepoMap) -> str:
    digest = hashlib.sha256()
    for item in sorted(repo_map.files, key=lambda current: current.path):
        digest.update(f"{item.path}\0{item.size_bytes}\0{item.mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()
