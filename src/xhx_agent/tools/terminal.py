from __future__ import annotations

import subprocess
from pathlib import Path

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


def run_terminal(workspace: Path, command: str, assume_yes: bool = False, timeout_seconds: int = 120) -> TerminalResult:
    decision = decide_terminal(command, assume_yes=assume_yes)
    if decision.decision != "allow":
        return TerminalResult(command=command, status=decision.decision, policy=decision)
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


def _summarize_output(stdout: str, stderr: str, max_lines: int = 20) -> str:
    output = (stdout + "\n" + stderr).strip()
    if not output:
        return "Command produced no output."
    lines = output.splitlines()
    return "\n".join(lines[-max_lines:])
