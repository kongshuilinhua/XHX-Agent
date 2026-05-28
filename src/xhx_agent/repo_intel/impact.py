from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map


class ImpactSummary(BaseModel):
    changed_files: list[str]
    impacted_tests: list[str] = Field(default_factory=list)
    verification_hints: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def analyze_impact(workspace: Path, changed_files: list[str], repo_map: RepoMap | None = None) -> ImpactSummary:
    repo_map = repo_map or build_repo_map(workspace)
    normalized = [path.replace("\\", "/") for path in changed_files if path]
    impacted_tests = sorted(set(_direct_test_for(path, repo_map) for path in normalized) - {""})
    notes: list[str] = []
    if not impacted_tests and any(_is_source(path) for path in normalized):
        notes.append("No direct test file mapping found for changed source files.")
    hints = list(repo_map.verification_hints)
    if impacted_tests and "python -m pytest" in hints:
        hints.insert(0, "python -m pytest " + " ".join(impacted_tests))
    return ImpactSummary(
        changed_files=normalized,
        impacted_tests=impacted_tests,
        verification_hints=_dedupe(hints),
        notes=notes,
    )


def _direct_test_for(path: str, repo_map: RepoMap) -> str:
    if path.startswith("tests/") or "/tests/" in path or Path(path).name.startswith("test_"):
        return path
    if not path.endswith(".py"):
        return ""
    stem = Path(path).stem
    candidates = [
        f"tests/test_{stem}.py",
        f"test_{stem}.py",
    ]
    existing = {item.path for item in repo_map.files}
    for candidate in candidates:
        if candidate in existing:
            return candidate
    return ""


def _is_source(path: str) -> bool:
    return Path(path).suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx"}


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
