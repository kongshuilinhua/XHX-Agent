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
    python_impacted_tests = [path for path in impacted_tests if path.endswith(".py")]
    if python_impacted_tests and "python -m pytest" in hints:
        hints.insert(0, "python -m pytest " + " ".join(python_impacted_tests))
    if impacted_tests and "npm test" in hints:
        notes.append("Direct JS/TS tests were mapped; npm test remains the portable verification command.")
    return ImpactSummary(
        changed_files=normalized,
        impacted_tests=impacted_tests,
        verification_hints=_dedupe(hints),
        notes=notes,
    )


def _direct_test_for(path: str, repo_map: RepoMap) -> str:
    if _is_test_path(path):
        return path
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return _python_direct_test(path, repo_map)
    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return _js_ts_direct_test(path, repo_map)
    return ""


def _python_direct_test(path: str, repo_map: RepoMap) -> str:
    stem = Path(path).stem
    return _first_existing(
        [
            f"tests/test_{stem}.py",
            f"test_{stem}.py",
        ],
        repo_map,
    )


def _js_ts_direct_test(path: str, repo_map: RepoMap) -> str:
    source = Path(path)
    stem = source.stem
    suffix = source.suffix.lower()
    if suffix not in {".js", ".jsx", ".ts", ".tsx"}:
        return ""
    test_suffixes = [suffix]
    if suffix == ".ts":
        test_suffixes.append(".js")
    if suffix == ".tsx":
        test_suffixes.extend([".ts", ".jsx", ".js"])
    candidates = [
        f"{folder}/{stem}{marker}{candidate_suffix}"
        for folder in ("test", "tests")
        for marker in (".test", ".spec")
        for candidate_suffix in test_suffixes
    ]
    candidates.extend(
        f"{source.parent.as_posix()}/{stem}{marker}{candidate_suffix}"
        for marker in (".test", ".spec")
        for candidate_suffix in test_suffixes
    )
    return _first_existing(candidates, repo_map)


def _first_existing(candidates: list[str], repo_map: RepoMap) -> str:
    existing = {item.path for item in repo_map.files}
    for candidate in candidates:
        if candidate in existing:
            return candidate
    return ""


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    return (
        normalized.startswith("test/")
        or normalized.startswith("tests/")
        or "/test/" in normalized
        or "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_source(path: str) -> bool:
    return Path(path).suffix.lower() in {".py", ".js", ".jsx", ".ts", ".tsx"}


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
