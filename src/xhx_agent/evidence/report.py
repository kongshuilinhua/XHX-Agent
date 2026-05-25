from __future__ import annotations

from pathlib import Path

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir


def write_report(
    workspace: Path,
    run_id: str,
    task: str,
    plan: list[str],
    changed_files: list[str],
    commands: list[str],
    verification: str,
    risks: list[str],
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

## Evidence Summary

- v0.1 summary generated from runtime tool and verification summaries.

## Risks

{_list(risks)}
"""
    path.write_text(content, encoding="utf-8")
    return path


def _list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- none"
