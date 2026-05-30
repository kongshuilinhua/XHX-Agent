from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ProjectScan(BaseModel):
    root: str
    detected_languages: list[str] = Field(default_factory=list)
    python: dict[str, bool] = Field(default_factory=dict)
    node: dict[str, object] = Field(default_factory=dict)
    file_count: int = 0


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".xhx",
    # Cache / tool directories that should never be indexed
    ".tmp",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    # IDE / editor directories
    ".idea",
    ".gemini",
    ".vscode",
}


def scan_project(workspace: Path) -> ProjectScan:
    files = [path for path in workspace.rglob("*") if path.is_file() and not _is_ignored(path, workspace)]
    languages: set[str] = set()
    if any(path.suffix == ".py" for path in files) or (workspace / "pyproject.toml").exists():
        languages.add("python")
    if (workspace / "package.json").exists() or any(path.suffix in {".js", ".jsx"} for path in files):
        languages.add("javascript")
    if (workspace / "tsconfig.json").exists() or any(path.suffix in {".ts", ".tsx"} for path in files):
        languages.add("typescript")

    node_scripts: dict[str, str] = {}
    package_json = workspace / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            node_scripts = dict(data.get("scripts", {}))
        except json.JSONDecodeError:
            node_scripts = {}

    return ProjectScan(
        root=str(workspace),
        detected_languages=sorted(languages),
        python={
            "pyproject": (workspace / "pyproject.toml").exists(),
            "pytest_ini": (workspace / "pytest.ini").exists(),
            "requirements": (workspace / "requirements.txt").exists(),
            "tests_dir": (workspace / "tests").exists(),
        },
        node={
            "package_json": package_json.exists(),
            "tsconfig": (workspace / "tsconfig.json").exists(),
            "scripts": node_scripts,
        },
        file_count=len(files),
    )


def _is_ignored(path: Path, workspace: Path) -> bool:
    relative = path.relative_to(workspace)
    return any(part in IGNORED_DIRS for part in relative.parts)
