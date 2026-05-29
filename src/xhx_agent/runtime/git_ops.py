from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel


class DiffSummary(BaseModel):
    changed_files: list[str]
    summary: str
    diff_text: str = ""
    truncated: bool = False
    risk_summary: list[str] = []


def _is_git_worktree(workspace: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


class GitOps:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def diff_changed_files(self, changed_files: list[str], max_chars: int = 12_000) -> DiffSummary:
        normalized_changed_files = sorted({item for item in changed_files if item})
        if not normalized_changed_files:
            return DiffSummary(
                changed_files=[],
                summary="No changed files.",
                diff_text="",
                truncated=False,
                risk_summary=[],
            )
        risks: list[str] = []
        diff_text = ""
        summary = f"{len(normalized_changed_files)} changed file(s)."
        if max_chars < 0:
            max_chars = 0
        if not _is_git_worktree(self.workspace):
            return DiffSummary(
                changed_files=normalized_changed_files,
                summary=f"{summary} Git diff unavailable outside a git worktree.",
                diff_text="",
                truncated=False,
                risk_summary=["Git diff unavailable outside a git worktree."],
            )
        try:
            completed = subprocess.run(
                ["git", "diff", "--", *normalized_changed_files],
                cwd=self.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            risks.append(f"Git diff unavailable: {exc}")
            return DiffSummary(
                changed_files=normalized_changed_files,
                summary=f"{summary} Git diff unavailable.",
                diff_text="",
                truncated=False,
                risk_summary=risks,
            )
        if completed.returncode != 0:
            message = completed.stderr.strip() or f"git diff exited with code {completed.returncode}"
            risks.append(message)
            return DiffSummary(
                changed_files=normalized_changed_files,
                summary=f"{summary} Git diff unavailable.",
                diff_text="",
                truncated=False,
                risk_summary=risks,
            )
        diff_text = completed.stdout
        truncated = len(diff_text) > max_chars
        if truncated:
            diff_text = diff_text[:max_chars]
            risks.append(f"Diff output truncated to {max_chars} characters.")
        if not diff_text:
            risks.append("No git diff output for the selected changed files.")
        return DiffSummary(
            changed_files=normalized_changed_files,
            summary=summary,
            diff_text=diff_text,
            truncated=truncated,
            risk_summary=risks,
        )
