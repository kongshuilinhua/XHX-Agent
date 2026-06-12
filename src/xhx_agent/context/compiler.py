"""上下文包编译器：每次模型调用前，把候选上下文按优先级塞进固定 token 预算。

流程：收集候选（项目地图 / 命中的 skill / 符号·引用·调用·import 上下文 / 变更文件 / 工具历史 /
最近错误 / 证据）→ 各自带 priority → 从高到低塞，超预算就丢。token 用 tiktoken 精确计、失败回退字符
启发式。长循环里旧的工具历史会被压成一行而非丢弃（见 _compact_tool_summaries）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xhx_agent.context.pack import ContextDebugRecord, ContextDebugReport, ContextItem, ContextPack
from xhx_agent.evidence.store import EvidenceEntry
from xhx_agent.repo_intel.context_builder import build_context_for_symbols
from xhx_agent.repo_intel.index import RepoIntelIndex, load_repo_intel_index
from xhx_agent.repo_intel.references import Reference, search_references
from xhx_agent.repo_intel.scanner import ProjectScan
from xhx_agent.repo_intel.symbols import Symbol, search_symbols

logger = logging.getLogger(__name__)

# 预算与各类上下文的体积上限：这些 MAX_* 是控制「喂给模型多少」的旋钮，
# 共同保证上下文包不超 token 预算、各类内容也不互相挤占过多。
DEFAULT_CONTEXT_BUDGET_TOKENS = 6_000
DEFAULT_TOP_K_EVIDENCE = 8
MAX_CHANGED_FILES = 8
MAX_TOOL_SUMMARIES = 12
MAX_PROJECT_MAP_CHARS = 4_000
MAX_CHANGED_FILE_CHARS = 4_000
MAX_SYMBOL_CONTEXTS = 5
MAX_SYMBOL_CONTEXT_CHARS = 2_500
MAX_SYMBOL_QUERY_TERMS = 6
MAX_IMPORT_CONTEXT_FILES = 4
MAX_IMPORT_CONTEXT_SYMBOLS = 6
MAX_CALL_CONTEXTS = 5
MAX_CALL_CONTEXT_SYMBOLS = 6
MAX_REFERENCE_CONTEXTS = 6
MAX_REFERENCE_CONTEXT_CHARS = 1_200
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


def _memory_context_items(workspace: Path, task: str, recent_error: str | None) -> list[ContextItem]:
    """召回与当前任务相关的长期记忆，作为 context-pack 的一个来源（第 ④ 轴：跨会话时间）。"""
    from xhx_agent.memory.recall import recall_memories

    query = task if not recent_error else f"{task}\n{recent_error}"
    items: list[ContextItem] = []
    for record in recall_memories(workspace, query, limit=5):
        header = f"[{record.mtype}] {record.description}".strip()
        body = record.body.strip()
        content = f"{header}\n{body}".strip() if body else header
        items.append(
            ContextItem(
                kind=f"memory:{record.mtype}",
                source=record.name,
                content=content,
                priority=88,
                reason="Cross-session memory recalled by deterministic keyword overlap.",
            )
        )
    return items


def compile_context_pack(
    *,
    workspace: Path,
    task: str,
    scan: ProjectScan,
    changed_files: list[str] | None = None,
    tool_summaries: list[str] | None = None,
    plan_summaries: list[str] | None = None,
    evidence_entries: list[EvidenceEntry] | None = None,
    recent_error: str | None = None,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
    top_k_evidence: int = DEFAULT_TOP_K_EVIDENCE,
    history_summarizer: Callable[[list[str]], str] | None = None,
) -> ContextPack:
    """编译一份预算内的上下文包：收集带优先级的候选 → 按优先级塞进 budget_tokens → 超出则丢弃。"""
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

    # Dynamic local Skill matching and lazy loading for v0.8
    try:
        from xhx_agent.skills.loader import SkillLoader

        skill_loader = SkillLoader(workspace)
        matched_skills = skill_loader.match_skills(task)
        for skill in matched_skills:
            if skill.content:
                candidates.append(
                    ContextItem(
                        kind="skill",
                        source=skill.name,
                        content=skill.content,
                        priority=92,
                        reason=f"Dynamic skill '{skill.name}' triggered by task matching rules.",
                    )
                )
    except Exception as e:
        # Gracefully handle any issues with dynamic loading
        logger.warning("Failed to match and load skills: %s", e)

    try:
        for item in _memory_context_items(workspace, task, recent_error):
            candidates.append(item)
    except Exception as e:
        logger.warning("Failed to recall long-term memory: %s", e)

    repo_index = _load_repo_index(workspace)

    for item in _symbol_context_items(workspace, task, recent_error, repo_index):
        candidates.append(item)

    for item in _reference_context_items(workspace, task, recent_error, repo_index):
        candidates.append(item)

    for item in _call_context_items(workspace, task, changed_files or [], recent_error, repo_index):
        candidates.append(item)

    for item in _import_context_items(workspace, changed_files or [], recent_error, repo_index):
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
        compacted_summary, recent_summaries = _compact_tool_summaries(
            tool_summaries, MAX_TOOL_SUMMARIES, history_summarizer
        )
        summary_lines = [compacted_summary] if compacted_summary else []
        summary_lines.extend(f"- {summary}" for summary in recent_summaries)
        candidates.append(
            ContextItem(
                kind="tool_results",
                source="current_run",
                content="\n".join(summary_lines),
                priority=80,
                reason="Recent tool outputs (older ones compacted) summarize the current loop without loading Raw Trace.",
            )
        )

    if plan_summaries:
        candidates.append(
            ContextItem(
                kind="plan_summaries",
                source="previous_turns",
                content="\n".join(f"- {summary}" for summary in plan_summaries),
                priority=65,
                reason="Summary of previous turns plans provides history context.",
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

    import json

    all_evidence = list(evidence_entries or [])
    try:
        evidence_dir = workspace / ".xhx" / "evidence"
        if evidence_dir.exists():
            # Sort files by modification time descending (newest first)
            jsonl_files = sorted(evidence_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            # Limit loading to the latest 3 historical run files
            latest_files = jsonl_files[:3]
            entry_count = 0
            max_entries = 100  # Cap total history entries processed

            for path in latest_files:
                if entry_count >= max_entries:
                    break
                try:
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if entry_count >= max_entries:
                            break
                        if line.strip():
                            try:
                                data = json.loads(line)
                                entry = EvidenceEntry(**data)
                                all_evidence.append(entry)
                                entry_count += 1
                            except Exception as e:
                                logger.warning("Failed to parse evidence entry in file %s: %s", path.name, e)
                except Exception as e:
                    logger.warning("Failed to read evidence file %s: %s", path.name, e)
    except Exception as e:
        logger.warning("Failed to process evidence directory: %s", e)

    for evidence in _select_top_evidence(all_evidence, limit=top_k_evidence):
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
    # 按 (priority, source) 从高到低塞：装得下就纳入并累加 token，装不下记为 omitted。
    # 于是高优先级（recent_error=95、project_map=90 等）在预算紧张时优先保命。
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


def _symbol_context_items(
    workspace: Path,
    task: str,
    recent_error: str | None,
    repo_index: RepoIntelIndex | None = None,
) -> list[ContextItem]:
    queries = _symbol_query_terms(" ".join(part for part in [task, recent_error or ""] if part))
    if not queries:
        return []
    index = repo_index.symbol_index if repo_index else _load_symbol_index(workspace)
    if index is None:
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


def _import_context_items(
    workspace: Path,
    changed_files: list[str],
    recent_error: str | None,
    repo_index: RepoIntelIndex | None,
) -> list[ContextItem]:
    if repo_index is None:
        return []
    known_files = {item.path for item in repo_index.repo_map.files}
    changed_anchors = _context_anchor_files(changed_files, None, known_files)
    error_anchors = [
        path for path in _context_anchor_files([], recent_error, known_files) if path not in changed_anchors
    ]
    anchor_files = [*changed_anchors, *error_anchors]
    if not anchor_files:
        return []
    related_files = _related_import_files(repo_index, anchor_files)
    related_files = [*error_anchors, *related_files] if changed_anchors else [*anchor_files, *related_files]
    related_files = _unique_limited(related_files, MAX_IMPORT_CONTEXT_FILES)
    if not related_files:
        return []
    selected_symbols: list[Symbol] = []
    seen_symbols: set[tuple[str, str, int]] = set()
    for path in related_files:
        for symbol in repo_index.symbol_index.symbols:
            if symbol.path != path:
                continue
            key = (symbol.path, symbol.name, symbol.line)
            if key in seen_symbols:
                continue
            seen_symbols.add(key)
            selected_symbols.append(symbol)
            if len(selected_symbols) >= MAX_IMPORT_CONTEXT_SYMBOLS:
                break
        if len(selected_symbols) >= MAX_IMPORT_CONTEXT_SYMBOLS:
            break
    contexts = build_context_for_symbols(workspace, selected_symbols)
    return [
        ContextItem(
            kind="import_context",
            source=f"{context.symbol.path}:{context.symbol.line}:{context.symbol.name}",
            content=_limit_text(context.excerpt, MAX_SYMBOL_CONTEXT_CHARS),
            priority=87,
            reason="Selected from Repo Intelligence import graph around changed files or recent errors.",
        )
        for context in contexts
    ]


def _reference_context_items(
    workspace: Path,
    task: str,
    recent_error: str | None,
    repo_index: RepoIntelIndex | None,
) -> list[ContextItem]:
    if repo_index is None:
        return []
    queries = _symbol_query_terms(" ".join(part for part in [task, recent_error or ""] if part))
    if not queries:
        return []
    selected: list[Reference] = []
    seen: set[tuple[str, str, int]] = set()
    for query in queries:
        for reference in search_references(repo_index.reference_index, query, limit=MAX_REFERENCE_CONTEXTS):
            key = (reference.name, reference.path, reference.line)
            if key in seen:
                continue
            seen.add(key)
            selected.append(reference)
            if len(selected) >= MAX_REFERENCE_CONTEXTS:
                break
        if len(selected) >= MAX_REFERENCE_CONTEXTS:
            break
    return [
        ContextItem(
            kind="reference_context",
            source=f"{reference.path}:{reference.line}:{reference.name}",
            content=_limit_text(_reference_excerpt(workspace, reference), MAX_REFERENCE_CONTEXT_CHARS),
            priority=86,
            reason="Selected by Repo Intelligence lightweight text reference search from the current task.",
        )
        for reference in selected
    ]


def _call_context_items(
    workspace: Path,
    task: str,
    changed_files: list[str],
    recent_error: str | None,
    repo_index: RepoIntelIndex | None,
) -> list[ContextItem]:
    if repo_index is None or not repo_index.call_graph.root:
        return []
    queries = _symbol_query_terms(" ".join(part for part in [task, recent_error or ""] if part))
    known_files = {item.path for item in repo_index.repo_map.files}
    anchor_files = _context_anchor_files(changed_files, recent_error, known_files)
    selected_symbols: list[Symbol] = []
    seen: set[tuple[str, str, int]] = set()
    for edge in repo_index.call_graph.edges:
        if not _call_edge_matches(edge.caller, edge.callee, edge.caller_path, edge.callee_path, queries, anchor_files):
            continue
        for path, name, line in [
            (edge.caller_path, edge.caller.split(".")[-1], edge.caller_line),
            (edge.callee_path or "", edge.callee, edge.callee_line or 0),
        ]:
            if not path or not line:
                continue
            symbol = _find_symbol(repo_index.symbol_index.symbols, path, name, line)
            if symbol is None:
                continue
            key = (symbol.path, symbol.name, symbol.line)
            if key in seen:
                continue
            seen.add(key)
            selected_symbols.append(symbol)
            if len(selected_symbols) >= MAX_CALL_CONTEXT_SYMBOLS:
                break
        if len(selected_symbols) >= MAX_CALL_CONTEXT_SYMBOLS:
            break
    contexts = build_context_for_symbols(workspace, selected_symbols)
    return [
        ContextItem(
            kind="call_context",
            source=f"{context.symbol.path}:{context.symbol.line}:{context.symbol.name}",
            content=_limit_text(context.excerpt, MAX_SYMBOL_CONTEXT_CHARS),
            priority=87,
            reason="Selected from Repo Intelligence lightweight call graph.",
        )
        for context in contexts[:MAX_CALL_CONTEXTS]
    ]


def _call_edge_matches(
    caller: str,
    callee: str,
    caller_path: str,
    callee_path: str | None,
    queries: list[str],
    anchor_files: list[str],
) -> bool:
    if anchor_files and (caller_path in anchor_files or callee_path in anchor_files):
        return True
    lowered = f"{caller} {callee}".lower()
    return any(query in lowered for query in queries)


def _find_symbol(symbols: list[Symbol], path: str, name: str, line: int) -> Symbol | None:
    for symbol in symbols:
        if symbol.path == path and symbol.name == name and symbol.line == line:
            return symbol
    for symbol in symbols:
        if symbol.path == path and symbol.name == name:
            return symbol
    return None


def _load_repo_index(workspace: Path) -> RepoIntelIndex | None:
    try:
        return load_repo_intel_index(workspace)
    except OSError:
        return None


def _load_symbol_index(workspace: Path):
    repo_index = _load_repo_index(workspace)
    return repo_index.symbol_index if repo_index else None


def _context_anchor_files(changed_files: list[str], recent_error: str | None, known_files: set[str]) -> list[str]:
    anchors: list[str] = []
    for path in changed_files:
        normalized = path.replace("\\", "/").lstrip("./")
        if normalized in known_files and normalized not in anchors:
            anchors.append(normalized)
    for path in _file_path_candidates(recent_error or ""):
        if path in known_files and path not in anchors:
            anchors.append(path)
    return anchors


def _related_import_files(repo_index: RepoIntelIndex, anchor_files: list[str]) -> list[str]:
    related: list[str] = []
    for anchor in anchor_files:
        for edge in repo_index.import_graph.edges:
            if edge.target == anchor and edge.importer not in related and edge.importer not in anchor_files:
                related.append(edge.importer)
            if edge.importer == anchor and edge.target not in related and edge.target not in anchor_files:
                related.append(edge.target)
            if len(related) >= MAX_IMPORT_CONTEXT_FILES:
                return related
    return related


def _unique_limited(items: list[str], limit: int) -> list[str]:
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _reference_excerpt(workspace: Path, reference: Reference, context_lines: int = 1) -> str:
    path = (workspace / reference.path).resolve()
    if not _is_inside(workspace, path) or not path.is_file():
        return reference.excerpt
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return reference.excerpt
    start = max(1, reference.line - context_lines)
    end = min(len(lines), reference.line + context_lines)
    return "\n".join(f"{line_number}: {lines[line_number - 1]}" for line_number in range(start, end + 1))


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


def _file_path_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    current = []
    allowed = set("./\\-_")
    for char in text:
        if char.isascii() and (char.isalnum() or char in allowed):
            current.append(char)
        elif current:
            _append_path_candidate(candidates, "".join(current))
            current.clear()
    if current:
        _append_path_candidate(candidates, "".join(current))
    return candidates


def _append_path_candidate(candidates: list[str], raw: str) -> None:
    normalized = raw.strip(".,:;()[]{}'\"").replace("\\", "/").lstrip("./")
    if "/" not in normalized or "." not in Path(normalized).name:
        return
    if normalized not in candidates:
        candidates.append(normalized)


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


def _heuristic_compaction(older: list[str]) -> str:
    """把更早的工具历史按工具名聚合成一行，附成功/失败计数。"""

    tool_counts: dict[str, int] = {}
    failed = 0
    for summary in older:
        parts = summary.split(":", 2)
        tool = parts[0].strip() or "other"
        tool_counts[tool] = tool_counts.get(tool, 0) + 1
        if len(parts) >= 2 and "fail" in parts[1].lower():
            failed += 1
    breakdown = ", ".join(
        f"{tool}×{count}" for tool, count in sorted(tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    fail_note = f", {failed} failed" if failed else ""
    return f"[compacted {len(older)} earlier tool results — {breakdown}{fail_note}]"


def _compact_tool_summaries(
    summaries: list[str], keep_recent: int, summarizer: Callable[[list[str]], str] | None = None
) -> tuple[str | None, list[str]]:
    """压缩溢出的工具历史，让长循环仍保留早期工作的痕迹。

    超过 keep_recent 的更早历史被压成一行：没有 summarizer 时是纯启发式计数（工具次数 + 失败数）；
    传了 summarizer（如 LLM 回调）则生成语义摘要，出错时回退到启发式计数。
    """

    keep_recent = max(0, keep_recent)
    if len(summaries) <= keep_recent:
        return None, list(summaries)
    split = len(summaries) - keep_recent
    older = summaries[:split]
    recent = summaries[split:]
    if summarizer is not None:
        try:
            return f"[summary] {summarizer(older)}", list(recent)
        except Exception:
            logger.warning("History summarizer failed; falling back to heuristic compaction.")
    return _heuristic_compaction(older), list(recent)


_tiktoken_encoding: Any = None
_tiktoken_lock = threading.Lock()


def _estimate_tokens(text: str) -> int:
    global _tiktoken_encoding
    if _tiktoken_encoding is None:
        with _tiktoken_lock:
            if _tiktoken_encoding is None:
                try:
                    import tiktoken

                    _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    _tiktoken_encoding = False

    if _tiktoken_encoding:
        try:
            return len(_tiktoken_encoding.encode(text, disallowed_special=()))
        except Exception:
            pass

    # tiktoken 不可用时的字符级回退：ASCII 约 0.25 token/字符，非 ASCII（中文等）约 1.5。
    tokens = 0.0
    for char in text:
        if ord(char) > 127:
            tokens += 1.5
        else:
            tokens += 0.25
    return max(1, int(tokens))


def _is_inside(workspace: Path, target: Path) -> bool:
    root = workspace.resolve()
    return root == target or root in target.parents
