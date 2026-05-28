from __future__ import annotations

import ast
import json
import posixpath
import re
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map


class ImportEdge(BaseModel):
    importer: str
    target: str
    kind: str


class ImportGraph(BaseModel):
    root: str
    edges: list[ImportEdge] = Field(default_factory=list)


DEFAULT_IMPACT_DEPTH = 4


def build_import_graph(workspace: Path, repo_map: RepoMap | None = None) -> ImportGraph:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    known_files = {item.path for item in repo_map.files}
    tsconfig = _load_tsconfig_paths(root)
    edges: list[ImportEdge] = []
    for item in repo_map.files:
        path = root / item.path
        if item.language == "python":
            edges.extend(_python_import_edges(root, path, known_files))
        elif item.language in {"javascript", "typescript"}:
            edges.extend(_js_ts_import_edges(root, path, known_files, tsconfig))
    return ImportGraph(root=str(root), edges=sorted(edges, key=lambda edge: (edge.importer, edge.target, edge.kind)))


def impacted_tests_from_imports(
    graph: ImportGraph,
    changed_files: list[str],
    repo_map: RepoMap,
    *,
    max_depth: int = DEFAULT_IMPACT_DEPTH,
) -> list[str]:
    changed = {path.replace("\\", "/") for path in changed_files}
    test_files = {item.path for item in repo_map.files if item.kind == "test"}
    reverse_imports: dict[str, set[str]] = {}
    for edge in graph.edges:
        reverse_imports.setdefault(edge.target, set()).add(edge.importer)
    impacted_tests: set[str] = set()
    visited = set(changed)
    frontier = set(changed)
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: set[str] = set()
        for target in frontier:
            for importer in reverse_imports.get(target, set()):
                if importer in visited:
                    continue
                visited.add(importer)
                if importer in test_files:
                    impacted_tests.add(importer)
                next_frontier.add(importer)
        frontier = next_frontier
        depth += 1
    return sorted(impacted_tests)


def _python_import_edges(root: Path, path: Path, known_files: set[str]) -> list[ImportEdge]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    importer = path.relative_to(root).as_posix()
    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _resolve_python_module(alias.name, importer, known_files)
                if target:
                    edges.append(ImportEdge(importer=importer, target=target, kind="python_import"))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            target = _resolve_python_module(module, importer, known_files)
            if target:
                edges.append(ImportEdge(importer=importer, target=target, kind="python_from"))
    return edges


_JS_IMPORT_PATTERNS = [
    re.compile(r"""from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""^\s*import\s+['"]([^'"]+)['"]"""),
    re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
]


def _js_ts_import_edges(root: Path, path: Path, known_files: set[str], tsconfig: _TsConfigPaths) -> list[ImportEdge]:
    importer = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    edges: list[ImportEdge] = []
    for pattern in _JS_IMPORT_PATTERNS:
        for match in pattern.finditer(text):
            specifier = match.group(1)
            target = _resolve_relative_import(specifier, importer, known_files) or _resolve_tsconfig_import(
                specifier,
                importer,
                known_files,
                tsconfig,
            )
            if target:
                edges.append(ImportEdge(importer=importer, target=target, kind="js_import"))
    return edges


def _resolve_python_module(module: str, importer: str, known_files: set[str]) -> str:
    if not module:
        return ""
    if module.startswith("."):
        level = len(module) - len(module.lstrip("."))
        rest = module[level:]
        base_parts = Path(importer).parent.parts
        if level > 1:
            base_parts = base_parts[: max(0, len(base_parts) - (level - 1))]
        module_parts = [part for part in rest.split(".") if part]
        candidates = [
            "/".join([*base_parts, *module_parts]) + ".py",
            "/".join([*base_parts, *module_parts, "__init__.py"]),
        ]
    else:
        module_parts = [part for part in module.split(".") if part]
        candidates = [
            "/".join(module_parts) + ".py",
            "/".join([*module_parts, "__init__.py"]),
            "src/" + "/".join(module_parts) + ".py",
            "src/" + "/".join([*module_parts, "__init__.py"]),
        ]
    return _first_known(candidates, known_files)


def _resolve_relative_import(specifier: str, importer: str, known_files: set[str]) -> str:
    if not specifier.startswith("."):
        return ""
    base = Path(importer).parent
    raw = posixpath.normpath((base / specifier).as_posix())
    candidates = [raw]
    for suffix in _js_ts_suffix_order(Path(importer).suffix.lower()):
        candidates.append(raw + suffix)
        candidates.append(raw + "/index" + suffix)
    return _first_known(candidates, known_files)


def _js_ts_suffix_order(importer_suffix: str) -> tuple[str, ...]:
    if importer_suffix in {".ts", ".tsx"}:
        return (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    if importer_suffix in {".mjs", ".cjs"}:
        return (importer_suffix, ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
    return (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")


class _TsConfigPaths(BaseModel):
    base_url: str = "."
    paths: dict[str, list[str]] = Field(default_factory=dict)


def _load_tsconfig_paths(root: Path) -> _TsConfigPaths:
    path = root / "tsconfig.json"
    if not path.exists():
        return _TsConfigPaths()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _TsConfigPaths()
    compiler_options = data.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return _TsConfigPaths()
    base_url = compiler_options.get("baseUrl", ".")
    paths = compiler_options.get("paths", {})
    if not isinstance(base_url, str):
        base_url = "."
    if not isinstance(paths, dict):
        paths = {}
    normalized_paths: dict[str, list[str]] = {}
    for alias, targets in paths.items():
        if not isinstance(alias, str) or not isinstance(targets, list):
            continue
        normalized_targets = [target for target in targets if isinstance(target, str)]
        if normalized_targets:
            normalized_paths[alias] = normalized_targets
    return _TsConfigPaths(base_url=base_url, paths=normalized_paths)


def _resolve_tsconfig_import(
    specifier: str,
    importer: str,
    known_files: set[str],
    tsconfig: _TsConfigPaths,
) -> str:
    if specifier.startswith("."):
        return ""
    candidates: list[str] = []
    for alias, targets in tsconfig.paths.items():
        match = _match_ts_path_alias(alias, specifier)
        if match is None:
            continue
        for target in targets:
            expanded = _expand_ts_path_target(target, match)
            candidates.extend(_js_ts_candidate_paths(posixpath.join(tsconfig.base_url, expanded), importer))
    candidates.extend(_js_ts_candidate_paths(posixpath.join(tsconfig.base_url, specifier), importer))
    return _first_known(candidates, known_files)


def _match_ts_path_alias(alias: str, specifier: str) -> str | None:
    if "*" not in alias:
        return "" if alias == specifier else None
    prefix, suffix = alias.split("*", 1)
    if not specifier.startswith(prefix) or not specifier.endswith(suffix):
        return None
    return specifier[len(prefix) : len(specifier) - len(suffix) if suffix else len(specifier)]


def _expand_ts_path_target(target: str, wildcard: str) -> str:
    if "*" in target:
        return target.replace("*", wildcard, 1)
    return target


def _js_ts_candidate_paths(raw: str, importer: str) -> list[str]:
    normalized = posixpath.normpath(raw.replace("\\", "/")).lstrip("./")
    candidates = [normalized]
    for suffix in _js_ts_suffix_order(Path(importer).suffix.lower()):
        candidates.append(normalized + suffix)
        candidates.append(normalized + "/index" + suffix)
    return candidates


def _first_known(candidates: list[str], known_files: set[str]) -> str:
    normalized = [
        posixpath.normpath(candidate.replace("\\", "/")).lstrip("./")
        for candidate in candidates
        if candidate
    ]
    for candidate in normalized:
        if candidate in known_files:
            return candidate
    return ""
