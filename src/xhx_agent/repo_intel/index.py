"""代码智能索引：构建 / 落盘 / 增量更新 RepoIntelIndex（repo map + 符号 + import + 引用 + 调用图）。

write_repo_intel_index 写 JSON 主索引并同步 SQLite 镜像；load_repo_intel_index 优先读 JSON，
指纹过期时走 incremental_update（按 size/mtime 只重算变更文件），缺失/损坏时整体重建。
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from xhx_agent.repo_intel.calls import (
    CallGraph,
    _js_ts_call_edges,
    _python_call_edges,
    _SymbolResolver,
    build_call_graph,
)
from xhx_agent.repo_intel.db import sync_index_to_sqlite
from xhx_agent.repo_intel.imports import (
    ImportGraph,
    _js_ts_import_edges,
    _load_tsconfig_paths,
    _python_import_edges,
    build_import_graph,
)
from xhx_agent.repo_intel.references import (
    DEFAULT_MAX_LINES_PER_FILE,
    DEFAULT_MAX_REFERENCES,
    DEFAULT_MAX_REFERENCES_PER_SYMBOL,
    Reference,
    ReferenceIndex,
    build_reference_index,
)
from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map
from xhx_agent.repo_intel.symbols import SymbolIndex, build_symbol_index, extract_file_symbols
from xhx_agent.repo_intel.types import RepoIntelDiagnostics, RepoIntelIndex

logger = logging.getLogger(__name__)


def build_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    root = workspace.resolve()
    repo_map = build_repo_map(root)
    symbol_index = build_symbol_index(root, repo_map)
    import_graph = build_import_graph(root, repo_map)
    reference_index = build_reference_index(root, repo_map, symbol_index)
    call_graph = build_call_graph(root, repo_map, symbol_index)
    return RepoIntelIndex(
        created_at=datetime.now(UTC).isoformat(),
        content_fingerprint=repo_map_fingerprint(repo_map),
        repo_map=repo_map,
        symbol_index=symbol_index,
        import_graph=import_graph,
        reference_index=reference_index,
        call_graph=call_graph,
    )


def repo_index_path(workspace: Path) -> Path:
    return workspace / ".xhx" / "repo" / "index.json"


def write_repo_intel_index(workspace: Path, index: RepoIntelIndex | None = None) -> Path:
    path = repo_index_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    index = index or build_repo_intel_index(workspace)
    path.write_text(index.model_dump_json(indent=2), encoding="utf-8")
    try:
        # JSON 是主索引；SQLite 只是同步出来的可查询镜像，同步失败不阻断主流程。
        sync_index_to_sqlite(workspace, index)
    except Exception as e:
        logger.warning("Failed to sync index to SQLite DB: %s", e)
    return path


def read_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    return RepoIntelIndex.model_validate_json(repo_index_path(workspace).read_text(encoding="utf-8"))


def _update_symbols(root: Path, old_symbols: list, changed_files: list, changed_or_deleted_paths: set) -> SymbolIndex:
    kept_symbols = [s for s in old_symbols if s.path not in changed_or_deleted_paths]
    new_symbols = []
    for file in changed_files:
        if file.kind in {"source", "test"}:
            new_symbols.extend(extract_file_symbols(root, file.path, file.language))
    merged_symbols = sorted(kept_symbols + new_symbols, key=lambda s: (s.path, s.line, s.name))
    return SymbolIndex(root=str(root), symbols=merged_symbols)


def _update_imports(
    root: Path, old_edges: list, changed_files: list, changed_or_deleted_paths: set, new_files_by_path: dict
) -> ImportGraph:
    kept_edges = [
        e
        for e in old_edges
        if e.importer not in changed_or_deleted_paths
        and e.importer in new_files_by_path
        and e.target in new_files_by_path
    ]
    known_files = set(new_files_by_path.keys())
    tsconfig = _load_tsconfig_paths(root)
    new_edges = []
    for file in changed_files:
        path = root / file.path
        if file.language == "python":
            new_edges.extend(_python_import_edges(root, path, known_files))
        elif file.language in {"javascript", "typescript"}:
            new_edges.extend(_js_ts_import_edges(root, path, known_files, tsconfig))
    merged_edges = sorted(kept_edges + new_edges, key=lambda e: (e.importer, e.target, e.kind))
    return ImportGraph(root=str(root), edges=merged_edges)


def _update_call_graph(
    root: Path,
    old_edges: list,
    changed_files: list,
    changed_or_deleted_paths: set,
    new_files_by_path: dict,
    symbol_index: SymbolIndex,
    max_edges: int,
) -> CallGraph:
    resolver = _SymbolResolver(symbol_index.symbols)
    kept_call_edges = [
        e
        for e in old_edges
        if e.caller_path not in changed_or_deleted_paths
        and e.caller_path in new_files_by_path
        and (e.callee_path is None or e.callee_path in new_files_by_path)
    ]
    new_call_edges = []
    for file in changed_files:
        if file.kind not in {"source", "test"}:
            continue
        path = root / file.path
        if file.language == "python":
            new_call_edges.extend(_python_call_edges(root, path, resolver))
        elif file.language in {"javascript", "typescript"}:
            file_symbols = [symbol for symbol in symbol_index.symbols if symbol.path == file.path]
            new_call_edges.extend(_js_ts_call_edges(root, path, file.language, file_symbols, resolver))

    merged_call_edges = sorted(
        kept_call_edges + new_call_edges, key=lambda e: (e.caller_path, e.caller_line, e.call_line, e.callee)
    )
    call_graph_truncated = len(merged_call_edges) >= max_edges
    if call_graph_truncated:
        merged_call_edges = merged_call_edges[:max_edges]
    return CallGraph(root=str(root), edges=merged_call_edges, truncated=call_graph_truncated, max_edges=max_edges)


def _update_references(
    root: Path,
    old_references: list,
    changed_files: list,
    changed_or_deleted_paths: set,
    symbol_index: SymbolIndex,
    skipped_files_old: list,
    reference_index_old,
) -> ReferenceIndex:
    max_references = reference_index_old.max_references or DEFAULT_MAX_REFERENCES
    max_references_per_symbol = reference_index_old.max_references_per_symbol or DEFAULT_MAX_REFERENCES_PER_SYMBOL
    max_lines_per_file = reference_index_old.max_lines_per_file or DEFAULT_MAX_LINES_PER_FILE

    kept_references = [r for r in old_references if r.path not in changed_or_deleted_paths]
    names = sorted({symbol.name for symbol in symbol_index.symbols if len(symbol.name) >= 3})
    definitions = {(symbol.name, symbol.path, symbol.line) for symbol in symbol_index.symbols}
    patterns = {name: re.compile(rf"\b{re.escape(name)}\b") for name in names}

    new_references: list[Reference] = []
    per_symbol_counts: dict[str, int] = {}
    for r in kept_references:
        per_symbol_counts[r.name] = per_symbol_counts.get(r.name, 0) + 1

    skipped_files = [p for p in skipped_files_old if p not in changed_or_deleted_paths]
    reference_truncated = len(kept_references) >= max_references

    if not reference_truncated and names:
        for file in changed_files:
            if file.kind not in {"source", "test"}:
                continue
            if len(kept_references) + len(new_references) >= max_references:
                reference_truncated = True
                break
            path = root / file.path
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            if len(lines) > max_lines_per_file:
                skipped_files.append(file.path)
                reference_truncated = True
                continue
            for line_number, line in enumerate(lines, start=1):
                for name, pattern in patterns.items():
                    if (name, file.path, line_number) in definitions:
                        continue
                    if per_symbol_counts.get(name, 0) >= max_references_per_symbol:
                        continue
                    if pattern.search(line):
                        new_references.append(
                            Reference(
                                name=name,
                                path=file.path,
                                line=line_number,
                                excerpt=line.strip(),
                            )
                        )
                        per_symbol_counts[name] = per_symbol_counts.get(name, 0) + 1
                        if len(kept_references) + len(new_references) >= max_references:
                            reference_truncated = True
                            break
                if len(kept_references) + len(new_references) >= max_references:
                    break

    merged_references = sorted(kept_references + new_references, key=lambda r: (r.name.lower(), r.path, r.line))
    if len(merged_references) > max_references:
        merged_references = merged_references[:max_references]
        reference_truncated = True

    return ReferenceIndex(
        root=str(root),
        references=merged_references,
        truncated=reference_truncated,
        skipped_files=sorted(set(skipped_files)),
        max_references=max_references,
        max_references_per_symbol=max_references_per_symbol,
        max_lines_per_file=max_lines_per_file,
    )


def incremental_update_repo_intel_index(workspace: Path, old_index: RepoIntelIndex) -> RepoIntelIndex:
    """增量更新索引：按文件 size/mtime 比对找出变更/删除，只重算这些文件，未变部分原样保留。"""
    root = workspace.resolve()
    new_repo_map = build_repo_map(root)

    old_files_by_path = {f.path: f for f in old_index.repo_map.files}
    new_files_by_path = {f.path: f for f in new_repo_map.files}

    changed_files = []
    for path, new_file in new_files_by_path.items():
        if path not in old_files_by_path:
            changed_files.append(new_file)
        else:
            old_file = old_files_by_path[path]
            if new_file.size_bytes != old_file.size_bytes or new_file.mtime_ns != old_file.mtime_ns:
                changed_files.append(new_file)

    deleted_paths = set(old_files_by_path.keys()) - set(new_files_by_path.keys())

    if not changed_files and not deleted_paths and old_index.reference_index.root and old_index.call_graph.root:
        return old_index

    changed_or_deleted_paths = {f.path for f in changed_files} | deleted_paths

    # 1. Update Symbols
    symbol_index = _update_symbols(root, old_index.symbol_index.symbols, changed_files, changed_or_deleted_paths)

    # 2. Update Imports
    import_graph = _update_imports(
        root, old_index.import_graph.edges, changed_files, changed_or_deleted_paths, new_files_by_path
    )

    # 3. Update Call Graph
    max_edges = old_index.call_graph.max_edges or 2000
    call_graph = _update_call_graph(
        root,
        old_index.call_graph.edges,
        changed_files,
        changed_or_deleted_paths,
        new_files_by_path,
        symbol_index,
        max_edges,
    )

    # 4. Update References
    reference_index = _update_references(
        root=root,
        old_references=old_index.reference_index.references,
        changed_files=changed_files,
        changed_or_deleted_paths=changed_or_deleted_paths,
        symbol_index=symbol_index,
        skipped_files_old=list(old_index.reference_index.skipped_files),
        reference_index_old=old_index.reference_index,
    )

    return RepoIntelIndex(
        created_at=datetime.now(UTC).isoformat(),
        content_fingerprint=repo_map_fingerprint(new_repo_map),
        repo_map=new_repo_map,
        symbol_index=symbol_index,
        import_graph=import_graph,
        reference_index=reference_index,
        call_graph=call_graph,
    )


def load_repo_intel_index(workspace: Path) -> RepoIntelIndex:
    try:
        index = read_repo_intel_index(workspace)
    except (OSError, ValueError):
        new_idx = build_repo_intel_index(workspace)
        write_repo_intel_index(workspace, new_idx)
        return new_idx
    current_repo_map = build_repo_map(workspace)
    # 指纹（文件 size+mtime 的哈希）变了才增量更新，避免每次都全量重扫。
    if index.content_fingerprint != repo_map_fingerprint(current_repo_map):
        updated_idx = incremental_update_repo_intel_index(workspace, index)
        write_repo_intel_index(workspace, updated_idx)
        return updated_idx
    if not index.reference_index.root or not index.call_graph.root:
        new_idx = build_repo_intel_index(workspace)
        write_repo_intel_index(workspace, new_idx)
        return new_idx
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
        reason = "Repo intelligence index reference_index section is not complete."
    elif not index.call_graph.root:
        status = "stale"
        reason = "Repo intelligence index call_graph section is not complete."
    return RepoIntelDiagnostics(
        path=str(path),
        status=status,
        schema_version=index.schema_version,
        size_bytes=size_bytes,
        file_count=len(index.repo_map.files),
        symbol_count=len(index.symbol_index.symbols),
        import_edge_count=len(index.import_graph.edges),
        call_edge_count=len(index.call_graph.edges),
        call_graph_truncated=index.call_graph.truncated,
        reference_count=len(index.reference_index.references),
        reference_truncated=index.reference_index.truncated,
        skipped_reference_files=list(index.reference_index.skipped_files),
        content_fingerprint=index.content_fingerprint,
        current_fingerprint=current_fingerprint,
        reason=reason,
    )


def repo_map_fingerprint(repo_map: RepoMap) -> str:
    digest = hashlib.sha256()
    for item in sorted(repo_map.files, key=lambda current: current.path):
        digest.update(f"{item.path}\0{item.size_bytes}\0{item.mtime_ns}\n".encode())
    return digest.hexdigest()
