from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.scanner import IGNORED_DIRS, ProjectScan, scan_project


class RepoFile(BaseModel):
    path: str
    language: str
    kind: str
    size_bytes: int


class RepoMap(BaseModel):
    root: str
    scan: ProjectScan
    files: list[RepoFile] = Field(default_factory=list)
    verification_hints: list[str] = Field(default_factory=list)


def build_repo_map(workspace: Path) -> RepoMap:
    root = workspace.resolve()
    scan = scan_project(root)
    files = [
        RepoFile(
            path=path.relative_to(root).as_posix(),
            language=_language_for(path),
            kind=_kind_for(path, root),
            size_bytes=path.stat().st_size,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and not _is_ignored(path, root)
    ]
    return RepoMap(
        root=str(root),
        scan=scan,
        files=files,
        verification_hints=_verification_hints(scan),
    )


def _language_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        return "javascript"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if path.name == "package.json":
        return "json"
    if path.name in {"pyproject.toml", "pytest.ini"} or suffix == ".toml":
        return "config"
    if suffix in {".md", ".mdx"}:
        return "markdown"
    return "unknown"


def _kind_for(path: Path, workspace: Path) -> str:
    relative = path.relative_to(workspace)
    parts = set(relative.parts[:-1])
    name = path.name
    if "tests" in parts or name.startswith("test_") or name.endswith("_test.py") or ".test." in name:
        return "test"
    if name in {"package.json", "pyproject.toml", "pytest.ini", "tsconfig.json"}:
        return "config"
    if path.suffix.lower() in {".md", ".mdx"}:
        return "docs"
    return "source"


def _verification_hints(scan: ProjectScan) -> list[str]:
    hints: list[str] = []
    if "python" in scan.detected_languages and (scan.python.get("tests_dir") or scan.python.get("pytest_ini")):
        hints.append("python -m pytest")
    scripts = scan.node.get("scripts", {})
    if isinstance(scripts, dict):
        if "test" in scripts:
            hints.append("npm test")
        if "typecheck" in scripts:
            hints.append("npm run typecheck")
        if "build" in scripts:
            hints.append("npm run build")
    return hints


def _is_ignored(path: Path, workspace: Path) -> bool:
    relative = path.relative_to(workspace)
    return any(part in IGNORED_DIRS for part in relative.parts)
