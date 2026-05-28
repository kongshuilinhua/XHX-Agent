from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from xhx_agent.repo_intel.impact import analyze_impact


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
    impact = analyze_impact(workspace, changed_files or [])
    if _is_python_project(workspace, changed_files):
        test_files = _python_test_files(changed_files)
        base_command = "python -m pytest"
        if test_files:
            commands.append(
                VerificationCommand(
                    command=f"{base_command} {' '.join(test_files)}",
                    reason="Python test file changed; run targeted pytest.",
                )
            )
        elif impact.impacted_tests:
            commands.append(
                VerificationCommand(
                    command=f"{base_command} {' '.join(impact.impacted_tests)}",
                    reason="Repo intelligence mapped changed source files to dependent tests.",
                )
            )
        else:
            commands.append(VerificationCommand(command=base_command, reason="Python project with tests or pytest config."))
    if package_json.exists():
        scripts = package_json.read_text(encoding="utf-8", errors="ignore")
        if '"test"' in scripts:
            reason = (
                "Repo intelligence mapped changed source files to dependent JS/TS tests; package.json defines test script."
                if _has_js_ts_impacted_tests(impact.impacted_tests)
                else "package.json defines test script."
            )
            commands.append(VerificationCommand(command="npm test", reason=reason))
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


def _python_test_files(changed_files: list[str] | None) -> list[str]:
    if not changed_files:
        return []
    return sorted(
        path.replace("\\", "/")
        for path in changed_files
        if path.replace("\\", "/").endswith(".py")
        and (
            path.replace("\\", "/").startswith("tests/")
            or Path(path).name.startswith("test_")
            or Path(path).name.endswith("_test.py")
        )
    )


def _has_js_ts_impacted_tests(paths: list[str]) -> bool:
    return any(Path(path).suffix.lower() in {".js", ".jsx", ".ts", ".tsx"} for path in paths)
