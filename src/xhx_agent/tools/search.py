from __future__ import annotations

import subprocess
from pathlib import Path


def search(workspace: Path, query: str, glob: str | None = None, max_results: int = 50) -> list[str]:
    command = ["rg", "--line-number", "--no-heading", query]
    if glob:
        command.extend(["--glob", glob])
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        lines = result.stdout.splitlines()
        return lines[:max_results]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _python_search(workspace, query, glob, max_results)


def _python_search(workspace: Path, query: str, glob: str | None, max_results: int) -> list[str]:
    pattern = glob or "*"
    results: list[str] = []
    for path in workspace.rglob(pattern):
        if not path.is_file() or any(
            part in {".git", ".venv", "node_modules"} for part in path.relative_to(workspace).parts
        ):
            continue
        try:
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if query in line:
                    results.append(f"{path.relative_to(workspace)}:{index}:{line}")
                    if len(results) >= max_results:
                        return results
        except UnicodeDecodeError:
            continue
    return results
