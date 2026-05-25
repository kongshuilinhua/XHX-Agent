from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from xhx_agent.safety.policy import PolicyDecision, decide_terminal


class TerminalResult(BaseModel):
    command: str
    status: str
    policy: PolicyDecision
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    summary: str = ""


ConfirmationCallback = Callable[[str, PolicyDecision], bool]


def run_terminal(
    workspace: Path,
    command: str,
    assume_yes: bool = False,
    timeout_seconds: int = 120,
    confirm_callback: ConfirmationCallback | None = None,
) -> TerminalResult:
    decision = decide_terminal(command, assume_yes=assume_yes)
    if decision.decision == "confirm" and confirm_callback is not None:
        if confirm_callback(command, decision):
            decision = decide_terminal(command, assume_yes=True)
        else:
            return TerminalResult(
                command=command,
                status="confirm",
                policy=decision,
                summary="User declined command confirmation.",
            )
    if decision.decision != "allow":
        return TerminalResult(
            command=command,
            status=decision.decision,
            policy=decision,
            summary=decision.reason,
        )
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            shell=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        summary = _summarize_output(stdout, stderr)
        return TerminalResult(
            command=command,
            status="failed",
            policy=decision,
            stdout=stdout,
            stderr=stderr,
            exit_code=None,
            summary=f"Command timed out after {timeout_seconds} seconds.\n{summary}".strip(),
        )
    stdout = completed.stdout
    stderr = completed.stderr
    summary = _summarize_output(stdout, stderr)
    return TerminalResult(
        command=command,
        status="success" if completed.returncode == 0 else "failed",
        policy=decision,
        stdout=stdout,
        stderr=stderr,
        exit_code=completed.returncode,
        summary=summary,
    )


def _summarize_output(stdout: str, stderr: str, max_lines: int = 20, max_chars: int = 4_000) -> str:
    output = (stdout + "\n" + stderr).strip()
    if not output:
        return "Command produced no output."
    lines = output.splitlines()
    summary = "\n".join(lines[-max_lines:])
    if len(summary) <= max_chars:
        return summary
    return "...<truncated>\n" + summary[-max_chars:]
