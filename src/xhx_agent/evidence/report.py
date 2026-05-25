from __future__ import annotations

from pathlib import Path

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir
from xhx_agent.safety.repair import RepairDecision
from xhx_agent.tools.terminal import TerminalResult


def write_report(
    workspace: Path,
    run_id: str,
    task: str,
    plan: list[str],
    changed_files: list[str],
    commands: list[str],
    verification: str,
    risks: list[str],
    verification_results: list[TerminalResult] | None = None,
    checkpoint_path: str | None = None,
    repair: RepairDecision | None = None,
) -> Path:
    ensure_xhx_dirs(workspace)
    path = xhx_dir(workspace) / "logbook" / f"{run_id}.md"
    content = f"""# xhx-agent Run Report

## Task

{task}

## Plan

{_list(plan)}

## Changed Files

{_list(changed_files)}

## Commands

{_list(commands)}

## Verification

{verification}

## Verification Details

{_verification_details(verification_results or [])}

## Checkpoint

{checkpoint_path or "- none"}

## Repair

{_repair_details(repair)}

## Evidence Summary

- Runtime summary generated from tool, policy, checkpoint, verification, and repair summaries.

## Risks

{_list(risks)}
"""
    path.write_text(content, encoding="utf-8")
    return path


def _list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- none"


def _verification_details(results: list[TerminalResult]) -> str:
    if not results:
        return "- none"
    sections: list[str] = []
    for result in results:
        exit_code = "none" if result.exit_code is None else str(result.exit_code)
        sections.append(
            "\n".join(
                [
                    f"### `{result.command}`",
                    "",
                    f"- status: {result.status}",
                    f"- risk: {result.policy.risk.value}",
                    f"- decision: {result.policy.decision}",
                    f"- exit_code: {exit_code}",
                    "- summary:",
                    "",
                    "```text",
                    result.summary or result.policy.reason,
                    "```",
                ]
            )
        )
    return "\n\n".join(sections)


def _repair_details(repair: RepairDecision | None) -> str:
    if repair is None:
        return "- none"
    return "\n".join(
        [
            f"- should_repair: {str(repair.should_repair).lower()}",
            f"- attempts_used: {repair.attempts_used}",
            f"- max_attempts: {repair.max_attempts}",
            f"- reason: {repair.reason}",
        ]
    )
