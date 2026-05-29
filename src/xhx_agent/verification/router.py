from __future__ import annotations

import json
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
        python_impacted_tests = _python_test_targets(impact.impacted_tests)
        base_command = "python -m pytest"
        if test_files:
            commands.append(
                VerificationCommand(
                    command=f"{base_command} {' '.join(test_files)}",
                    reason="Python test file changed; run targeted pytest.",
                )
            )
        elif python_impacted_tests:
            commands.append(
                VerificationCommand(
                    command=f"{base_command} {' '.join(python_impacted_tests)}",
                    reason="Repo intelligence mapped changed source files to dependent tests.",
                )
            )
        else:
            commands.append(
                VerificationCommand(command=base_command, reason="Python project with tests or pytest config.")
            )
    if package_json.exists():
        scripts = _package_scripts(package_json)
        test_script = scripts.get("test")
        if test_script:
            target_command = _targeted_node_test_command(test_script, impact.impacted_tests)
            reason = (
                "Repo intelligence mapped changed source files to dependent JS/TS tests; package.json defines test script."
                if _has_js_ts_impacted_tests(impact.impacted_tests)
                else "package.json defines test script."
            )
            if target_command:
                reason = "Repo intelligence mapped changed source files to a targeted JS/TS test command."
            commands.append(VerificationCommand(command=target_command or "npm test", reason=reason))
        elif "typecheck" in scripts:
            commands.append(
                VerificationCommand(command="npm run typecheck", reason="package.json defines typecheck script.")
            )
        elif "build" in scripts:
            commands.append(VerificationCommand(command="npm run build", reason="package.json defines build script."))
    if commands:
        return VerificationPlan(commands=commands)
    return VerificationPlan(commands=[], skip_reason="No safe verification command inferred.")


def _is_python_project(workspace: Path, changed_files: list[str] | None) -> bool:
    if changed_files:
        normalized = [path.replace("\\", "/") for path in changed_files]
        if any(path.endswith(".py") for path in normalized):
            return True
        if any(Path(path).name in {"pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"} for path in normalized):
            return (workspace / "tests").exists() or (workspace / "pytest.ini").exists()
        return False
    return bool((workspace / "tests").exists() or (workspace / "pytest.ini").exists())


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


def _python_test_targets(paths: list[str]) -> list[str]:
    return sorted(dict.fromkeys(path.replace("\\", "/") for path in paths if path.replace("\\", "/").endswith(".py")))


def _package_scripts(package_json: Path) -> dict[str, str]:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(name): str(command) for name, command in scripts.items() if isinstance(command, str)}


def _targeted_node_test_command(test_script: str, impacted_tests: list[str]) -> str | None:
    targets = _js_ts_test_targets(impacted_tests)
    if not targets:
        return None
    normalized_script = " ".join(test_script.lower().split())
    if _supports_node_test_targets(normalized_script):
        return "npm test -- " + " ".join(targets)
    return None


def _js_ts_test_targets(paths: list[str]) -> list[str]:
    return sorted(
        dict.fromkeys(
            path.replace("\\", "/") for path in paths if Path(path).suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}
        )
    )


def _supports_node_test_targets(normalized_script: str) -> bool:
    runners = ("vitest", "jest", "node --test")
    return any(runner in normalized_script for runner in runners)
