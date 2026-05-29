from __future__ import annotations

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.calls import CallGraph
from xhx_agent.repo_intel.imports import ImportGraph
from xhx_agent.repo_intel.references import ReferenceIndex
from xhx_agent.repo_intel.repo_map import RepoMap
from xhx_agent.repo_intel.symbols import SymbolIndex


class RepoIntelIndex(BaseModel):
    schema_version: int = 1
    created_at: str
    content_fingerprint: str
    repo_map: RepoMap
    symbol_index: SymbolIndex
    import_graph: ImportGraph
    reference_index: ReferenceIndex = Field(default_factory=lambda: ReferenceIndex(root=""))
    call_graph: CallGraph = Field(default_factory=lambda: CallGraph(root=""))


class RepoIntelDiagnostics(BaseModel):
    path: str
    status: str
    schema_version: int | None = None
    size_bytes: int = 0
    file_count: int = 0
    symbol_count: int = 0
    import_edge_count: int = 0
    call_edge_count: int = 0
    call_graph_truncated: bool = False
    reference_count: int = 0
    reference_truncated: bool = False
    skipped_reference_files: list[str] = Field(default_factory=list)
    content_fingerprint: str | None = None
    current_fingerprint: str | None = None
    reason: str = ""
