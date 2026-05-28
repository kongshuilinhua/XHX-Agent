from __future__ import annotations

from pathlib import Path
from typing import Any

from xhx_agent.context.pack import ContextDebugRecord, ContextDebugReport, ContextItem, ContextPack
from xhx_agent.evidence.store import EvidenceEntry
from xhx_agent.repo_intel.context_builder import build_context_for_symbols
from xhx_agent.repo_intel.index import load_repo_intel_index
from xhx_agent.repo_intel.scanner import ProjectScan
from xhx_agent.repo_intel.symbols import Symbol, search_symbols


DEFAULT_CONTEXT_BUDGET_TOKENS = 6_000
DEFAULT_TOP_K_EVIDENCE = 8
MAX_CHANGED_FILES = 8
MAX_TOOL_SUMMARIES = 12
MAX_PROJECT_MAP_CHARS = 4_000
MAX_CHANGED_FILE_CHARS = 4_000
MAX_SYMBOL_CONTEXTS = 5
MAX_SYMBOL_CONTEXT_CHARS = 2_500
MAX_SYMBOL_QUERY_TERMS = 6
SYMBOL_QUERY_STOPWORDS = {
    "and",
    "bug",
    "code",
    "error",
    "fail",
    "fix",
    "for",
    "from",
    "issue",
    "test",
    "the",
    "this",
    "with",
}


def compile_context_pack(
    *,
    workspace: Path,
    task: str,
    scan: ProjectScan,
    changed_files: list[str] | None = None,
    tool_summaries: list[str] | None = None,
    evidence_entries: list[EvidenceEntry] | None = None,
    recent_error: str | None = None,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    top_k_evidence: int = DEFAULT_TOP_K_EVIDENCE,
) -> ContextPack:
    candidates: list[ContextItem] = []

    xhx_md = workspace / "XHX.md"
    if xhx_md.exists():
        candidates.append(
            ContextItem(
                kind="project_map",
                source="XHX.md",
                content=_read_text_limited(xhx_md, max_chars=MAX_PROJECT_MAP_CHARS),
                priority=90,
                reason="Project rules and repository map are stable context.",
            )
        )

    for item in _symbol_context_items(workspace, task, recent_error):
        candidates.append(item)

    changed_selection = _select_changed_files(changed_files or [])
    for file_path in changed_selection.selected:
        target = (workspace / file_path).resolve()
        if target.is_file() and _is_inside(workspace, target):
            candidates.append(
                ContextItem(
                    kind="changed_file",
                    source=file_path,
                    content=_read_text_limited(target, max_chars=MAX_CHANGED_FILE_CHARS),
                    priority=85,
                    reason="Changed files are needed to continue or verify the current edit.",
                )
            )
    for file_path in changed_selection.omitted:
        candidates.append(
            ContextItem(
                kind="changed_file",
                source=file_path,
                content="",
                priority=84,
                reason="Omitted before token packing because changed file selection reached its limit.",
            )
        )

    if tool_summaries:
        candidates.append(
            ContextItem(
                kind="tool_results",
                source="current_run",
                content="\n".join(f"- {summary}" for summary in tool_summaries[-MAX_TOOL_SUMMARIES:]),
                priority=80,
                reason="Recent tool outputs summarize the current loop without loading Raw Trace.",
            )
        )

    if recent_error:
        candidates.append(
            ContextItem(
                kind="recent_error",
                source="current_run",
                content=recent_error,
                priority=95,
                reason="Recent failure drives repair planning and should survive budget pressure.",
            )
        )

    for evidence in _select_top_evidence(evidence_entries or [], limit=top_k_evidence):
        candidates.append(
            ContextItem(
                kind=f"evidence:{evidence.kind}",
                source=evidence.source,
                content=evidence.summary,
                priority=_evidence_priority(evidence),
                reason=f"Selected from Evidence Index with confidence={evidence.confidence:.2f}.",
            )
        )

    packed_items: list[ContextItem] = []
    omitted: list[str] = []
    debug_records: list[ContextDebugRecord] = []
    used_tokens = _estimate_tokens(task) + _estimate_tokens(str(scan.model_dump()))
    reserved_tokens = used_tokens
    for item in sorted(candidates, key=lambda current: (current.priority, current.source), reverse=True):
        item.tokens_estimate = _estimate_tokens(item.content)
        if not item.content:
            omitted.append(f"{item.kind}:{item.source}")
            debug_records.append(_debug_record(item, selected=False, reason=item.reason))
            continue
        if used_tokens + item.tokens_estimate <= budget_tokens:
            packed_items.append(item)
            used_tokens += item.tokens_estimate
            debug_records.append(_debug_record(item, selected=True, reason=item.reason))
        else:
            omitted.append(f"{item.kind}:{item.source}")
            debug_records.append(
                _debug_record(
                    item,
                    selected=False,
                    reason=f"Budget exceeded: needed {item.tokens_estimate}, available {max(0, budget_tokens - used_tokens)}.",
                )
            )

    return ContextPack(
        task=task,
        mode=_infer_mode(changed_files, recent_error),
        budget_tokens=budget_tokens,
        used_tokens_estimate=used_tokens,
        project_summary=_project_summary(scan),
        constraints=[
            "All repository writes must go through apply_patch.",
            "Use relative paths only.",
            "Do not place the full Raw Trace in the model context.",
            "If evidence is insufficient, search/read_file first instead of patching.",
            "Do not generate terminal verification commands; the Verification Router handles verification.",
        ],
        items=packed_items,
        omitted=omitted,
        debug=ContextDebugReport(
            budget_tokens=budget_tokens,
            used_tokens_estimate=used_tokens,
            reserved_tokens_estimate=reserved_tokens,
            selected_count=len(packed_items),
            omitted_count=len(omitted),
            records=debug_records,
        ),
    )


def _project_summary(scan: ProjectScan) -> dict[str, Any]:
    return {
        "detected_languages": scan.detected_languages,
        "file_count": scan.file_count,
        "python": scan.python,
        "node": scan.node,
    }


def _symbol_context_items(workspace: Path, task: str, recent_error: str | None) -> list[ContextItem]:
    queries = _symbol_query_terms(" ".join(part for part in [task, recent_error or ""] if part))
    if not queries:
        return []
    try:
        index = _load_symbol_index(workspace)
    except OSError:
        return []
    selected: list[Symbol] = []
    seen: set[tuple[str, str, int]] = set()
    for query in queries:
        for symbol in search_symbols(index, query, limit=MAX_SYMBOL_CONTEXTS):
            key = (symbol.path, symbol.name, symbol.line)
            if key in seen:
                continue
            seen.add(key)
            selected.append(symbol)
            if len(selected) >= MAX_SYMBOL_CONTEXTS:
                break
        if len(selected) >= MAX_SYMBOL_CONTEXTS:
            break
    contexts = build_context_for_symbols(workspace, selected)
    return [
        ContextItem(
            kind="symbol_context",
            source=f"{context.symbol.path}:{context.symbol.line}:{context.symbol.name}",
            content=_limit_text(context.excerpt, MAX_SYMBOL_CONTEXT_CHARS),
            priority=88,
            reason="Selected by Repo Intelligence symbol search from the current task.",
        )
        for context in contexts
    ]


def _load_symbol_index(workspace: Path):
    return load_repo_intel_index(workspace).symbol_index


def _symbol_query_terms(text: str) -> list[str]:
    terms: list[str] = []
    for raw in _identifier_candidates(text):
        lowered = raw.lower()
        if lowered in SYMBOL_QUERY_STOPWORDS or len(raw) < 3:
            continue
        if lowered not in terms:
            terms.append(lowered)
        if len(terms) >= MAX_SYMBOL_QUERY_TERMS:
            break
    return terms


def _identifier_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    current = []
    for char in text:
        if char.isascii() and (char.isalnum() or char == "_"):
            current.append(char)
        elif current:
            candidates.append("".join(current))
            current.clear()
    if current:
        candidates.append("".join(current))
    return candidates


def _infer_mode(changed_files: list[str] | None, recent_error: str | None) -> str:
    if recent_error:
        return "repair-loop"
    if changed_files:
        return "linear-edit"
    return "research-or-edit"


class _Selection:
    def __init__(self, selected: list[str], omitted: list[str]) -> None:
        self.selected = selected
        self.omitted = omitted


def _select_changed_files(changed_files: list[str]) -> _Selection:
    unique = list(dict.fromkeys(changed_files))
    selected = unique[-MAX_CHANGED_FILES:]
    omitted = unique[: max(0, len(unique) - MAX_CHANGED_FILES)]
    return _Selection(selected=selected, omitted=omitted)


def _select_top_evidence(entries: list[EvidenceEntry], limit: int) -> list[EvidenceEntry]:
    deduped: dict[tuple[str, str, str], EvidenceEntry] = {}
    for entry in entries:
        deduped[(entry.kind, entry.source, entry.summary)] = entry
    ranked = sorted(
        deduped.values(),
        key=lambda entry: (_evidence_priority(entry), entry.created_at),
        reverse=True,
    )
    return ranked[:limit]


def _evidence_priority(entry: EvidenceEntry) -> int:
    kind_weight = {
        "error": 35,
        "test": 30,
        "patch": 25,
        "decision": 20,
        "file": 15,
        "checkpoint": 10,
        "policy": 5,
    }.get(entry.kind, 10)
    confidence_weight = int(max(0.0, min(entry.confidence, 1.0)) * 40)
    return 40 + kind_weight + confidence_weight


def _debug_record(item: ContextItem, *, selected: bool, reason: str) -> ContextDebugRecord:
    return ContextDebugRecord(
        kind=item.kind,
        source=item.source,
        priority=item.priority,
        tokens_estimate=item.tokens_estimate,
        selected=selected,
        reason=reason,
    )


def _read_text_limited(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _limit_text(text, max_chars)


def _limit_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<truncated>"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _is_inside(workspace: Path, target: Path) -> bool:
    root = workspace.resolve()
    return root == target or root in target.parents
