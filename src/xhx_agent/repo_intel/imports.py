from __future__ import annotations

import ast
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


def build_import_graph(workspace: Path, repo_map: RepoMap | None = None) -> ImportGraph:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    known_files = {item.path for item in repo_map.files}
    edges: list[ImportEdge] = []
    for item in repo_map.files:
        path = root / item.path
        if item.language == "python":
            edges.extend(_python_import_edges(root, path, known_files))
        elif item.language in {"javascript", "typescript"}:
            edges.extend(_js_ts_import_edges(root, path, known_files))
    return ImportGraph(root=str(root), edges=sorted(edges, key=lambda edge: (edge.importer, edge.target, edge.kind)))


def impacted_tests_from_imports(graph: ImportGraph, changed_files: list[str], repo_map: RepoMap) -> list[str]:
    changed = {path.replace("\\", "/") for path in changed_files}
    test_files = {item.path for item in repo_map.files if item.kind == "test"}
    return sorted(
        edge.importer
        for edge in graph.edges
        if edge.target in changed and edge.importer in test_files
    )


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


def _js_ts_import_edges(root: Path, path: Path, known_files: set[str]) -> list[ImportEdge]:
    importer = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    edges: list[ImportEdge] = []
    for pattern in _JS_IMPORT_PATTERNS:
        for match in pattern.finditer(text):
            specifier = match.group(1)
            target = _resolve_relative_import(specifier, importer, known_files)
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
    for suffix in (".js", ".jsx", ".ts", ".tsx"):
        candidates.append(raw + suffix)
        candidates.append(raw + "/index" + suffix)
    return _first_known(candidates, known_files)


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
