from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class VerificationCommand(BaseModel):
    command: str
    reason: str
    risk: str = "confirm"


class VerificationPlan(BaseModel):
    commands: list[VerificationCommand]
    skip_reason: str | None = None


def infer_verification(workspace: Path, changed_files: list[str] | None = None) -> VerificationPlan:
    commands: list[VerificationCommand] = []
    package_json = workspace / "package.json"
    if _is_python_project(workspace, changed_files):
        command = "uv run pytest" if (workspace / "pyproject.toml").exists() else "python -m pytest"
        commands.append(VerificationCommand(command=command, reason="Python project with tests or pytest config."))
    if package_json.exists():
        scripts = package_json.read_text(encoding="utf-8", errors="ignore")
        if '"test"' in scripts:
            commands.append(VerificationCommand(command="npm test", reason="package.json defines test script."))
        elif '"typecheck"' in scripts:
            commands.append(VerificationCommand(command="npm run typecheck", reason="package.json defines typecheck script."))
        elif '"build"' in scripts:
            commands.append(VerificationCommand(command="npm run build", reason="package.json defines build script."))
    if commands:
        return VerificationPlan(commands=commands)
    return VerificationPlan(commands=[], skip_reason="No safe verification command inferred.")


def _is_python_project(workspace: Path, changed_files: list[str] | None) -> bool:
    if (workspace / "tests").exists() or (workspace / "pytest.ini").exists():
        return True
    if (workspace / "pyproject.toml").exists() and changed_files and any(path.endswith(".py") for path in changed_files):
        return True
    return False
