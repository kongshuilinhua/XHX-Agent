from __future__ import annotations

from pathlib import Path
from typing import Any

from xhx_agent.context.pack import ContextItem, ContextPack
from xhx_agent.evidence.store import EvidenceEntry
from xhx_agent.repo_intel.scanner import ProjectScan


DEFAULT_CONTEXT_BUDGET_TOKENS = 6_000


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
) -> ContextPack:
    items: list[ContextItem] = []
    omitted: list[str] = []

    xhx_md = workspace / "XHX.md"
    if xhx_md.exists():
        items.append(
            ContextItem(
                kind="project_map",
                source="XHX.md",
                content=_read_text_limited(xhx_md, max_chars=4_000),
                priority=90,
            )
        )

    for file_path in changed_files or []:
        target = (workspace / file_path).resolve()
        if target.is_file() and _is_inside(workspace, target):
            items.append(
                ContextItem(
                    kind="changed_file",
                    source=file_path,
                    content=_read_text_limited(target, max_chars=4_000),
                    priority=85,
                )
            )

    if tool_summaries:
        items.append(
            ContextItem(
                kind="tool_results",
                source="current_run",
                content="\n".join(f"- {summary}" for summary in tool_summaries[-12:]),
                priority=80,
            )
        )

    if recent_error:
        items.append(
            ContextItem(
                kind="recent_error",
                source="current_run",
                content=recent_error,
                priority=95,
            )
        )

    for evidence in (evidence_entries or [])[-10:]:
        items.append(
            ContextItem(
                kind=f"evidence:{evidence.kind}",
                source=evidence.source,
                content=evidence.summary,
                priority=70,
            )
        )

    packed_items: list[ContextItem] = []
    used_tokens = _estimate_tokens(task) + _estimate_tokens(str(scan.model_dump()))
    for item in sorted(items, key=lambda current: current.priority, reverse=True):
        item.tokens_estimate = _estimate_tokens(item.content)
        if used_tokens + item.tokens_estimate <= budget_tokens:
            packed_items.append(item)
            used_tokens += item.tokens_estimate
        else:
            omitted.append(f"{item.kind}:{item.source}")

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
    )


def _project_summary(scan: ProjectScan) -> dict[str, Any]:
    return {
        "detected_languages": scan.detected_languages,
        "file_count": scan.file_count,
        "python": scan.python,
        "node": scan.node,
    }


def _infer_mode(changed_files: list[str] | None, recent_error: str | None) -> str:
    if recent_error:
        return "repair-loop"
    if changed_files:
        return "linear-edit"
    return "research-or-edit"


def _read_text_limited(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<truncated>"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _is_inside(workspace: Path, target: Path) -> bool:
    root = workspace.resolve()
    return root == target or root in target.parents
