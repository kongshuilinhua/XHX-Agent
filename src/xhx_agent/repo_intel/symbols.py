from __future__ import annotations

import ast
import re
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.repo_intel.repo_map import RepoMap, build_repo_map


class Symbol(BaseModel):
    name: str
    kind: str
    path: str
    line: int
    end_line: int | None = None
    language: str
    parent: str | None = None


class SymbolIndex(BaseModel):
    root: str
    symbols: list[Symbol] = Field(default_factory=list)


def extract_file_symbols(root: Path, rel_path: str, language: str) -> list[Symbol]:
    path = root / rel_path
    if language == "python":
        return _python_symbols(root, path)
    elif language in {"javascript", "typescript"}:
        return _js_ts_symbols(root, path, language)
    return []


def build_symbol_index(workspace: Path, repo_map: RepoMap | None = None) -> SymbolIndex:
    root = workspace.resolve()
    repo_map = repo_map or build_repo_map(root)
    symbols: list[Symbol] = []
    for item in repo_map.files:
        if item.kind not in {"source", "test"}:
            continue
        symbols.extend(extract_file_symbols(root, item.path, item.language))
    return SymbolIndex(root=str(root), symbols=sorted(symbols, key=lambda item: (item.path, item.line, item.name)))


def search_symbols(index: SymbolIndex, query: str, *, limit: int = 20) -> list[Symbol]:
    db_path = Path(index.root) / ".xhx" / "repo" / "index.db"
    if db_path.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            try:
                cursor = conn.cursor()
                lowered = query.lower()
                if not lowered:
                    cursor.execute(
                        "SELECT name, kind, path, line, end_line, language, parent FROM symbols ORDER BY path, line, name LIMIT ?",
                        (limit,),
                    )
                    rows = cursor.fetchall()
                    return [
                        Symbol(
                            name=row[0],
                            kind=row[1],
                            path=row[2],
                            line=row[3],
                            end_line=row[4],
                            language=row[5],
                            parent=row[6],
                        )
                        for row in rows
                    ]

                # 1. Exact matches
                cursor.execute(
                    "SELECT name, kind, path, line, end_line, language, parent FROM symbols WHERE LOWER(name) = ? LIMIT ?",
                    (lowered, limit),
                )
                exact_rows = cursor.fetchall()
                results = [
                    Symbol(
                        name=row[0],
                        kind=row[1],
                        path=row[2],
                        line=row[3],
                        end_line=row[4],
                        language=row[5],
                        parent=row[6],
                    )
                    for row in exact_rows
                ]

                # 2. Prefix matches
                if len(results) < limit:
                    cursor.execute(
                        "SELECT name, kind, path, line, end_line, language, parent FROM symbols WHERE LOWER(name) LIKE ? AND LOWER(name) != ? LIMIT ?",
                        (lowered + "%", lowered, limit - len(results)),
                    )
                    prefix_rows = cursor.fetchall()
                    results.extend(
                        [
                            Symbol(
                                name=row[0],
                                kind=row[1],
                                path=row[2],
                                line=row[3],
                                end_line=row[4],
                                language=row[5],
                                parent=row[6],
                            )
                            for row in prefix_rows
                        ]
                    )

                # 3. Contains matches
                if len(results) < limit:
                    cursor.execute(
                        "SELECT name, kind, path, line, end_line, language, parent FROM symbols WHERE LOWER(name) LIKE ? AND LOWER(name) NOT LIKE ? LIMIT ?",
                        ("%" + lowered + "%", lowered + "%", limit - len(results)),
                    )
                    contains_rows = cursor.fetchall()
                    results.extend(
                        [
                            Symbol(
                                name=row[0],
                                kind=row[1],
                                path=row[2],
                                line=row[3],
                                end_line=row[4],
                                language=row[5],
                                parent=row[6],
                            )
                            for row in contains_rows
                        ]
                    )

                return results
            finally:
                conn.close()
        except Exception:
            # Fallback to standard in-memory list filtering on any SQLite connection or table error
            pass

    lowered = query.lower()
    if not lowered:
        return index.symbols[:limit]
    exact = [symbol for symbol in index.symbols if symbol.name.lower() == lowered]
    prefix = [symbol for symbol in index.symbols if symbol not in exact and symbol.name.lower().startswith(lowered)]
    contains = [
        symbol
        for symbol in index.symbols
        if symbol not in exact and symbol not in prefix and lowered in symbol.name.lower()
    ]
    return (exact + prefix + contains)[:limit]


def _python_symbols(root: Path, path: Path) -> list[Symbol]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    relative = path.relative_to(root).as_posix()
    symbols: list[Symbol] = []

    def visit(node: ast.AST, parent: str | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind="class",
                        path=relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", None),
                        language="python",
                        parent=parent,
                    )
                )
                visit(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind="function",
                        path=relative,
                        line=child.lineno,
                        end_line=getattr(child, "end_lineno", None),
                        language="python",
                        parent=parent,
                    )
                )
                visit(child, child.name if parent is None else f"{parent}.{child.name}")
            else:
                visit(child, parent)

    visit(tree)
    return symbols


_JS_TS_PATTERNS = [
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?[^=]*=>")),
]


def _traverse_ast(
    node,
    code_bytes: bytes,
    symbols: list[Symbol],
    relative_path: str,
    language_name: str,
    parent_name: str | None = None,
) -> None:
    node_type = node.type
    name = None
    kind = None

    if node_type == "class_declaration":
        kind = "class"
        for child in node.children:
            if child.type == "identifier":
                name = code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
                break
    elif node_type == "interface_declaration":
        kind = "interface"
        for child in node.children:
            if child.type == "identifier":
                name = code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
                break
    elif node_type == "type_alias_declaration":
        kind = "type"
        for child in node.children:
            if child.type == "identifier":
                name = code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
                break
    elif node_type in ("function_declaration", "generator_function_declaration"):
        kind = "function"
        for child in node.children:
            if child.type == "identifier":
                name = code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
                break
    elif node_type == "method_definition":
        kind = "function"
        for child in node.children:
            if child.type in ("property_identifier", "identifier"):
                name = code_bytes[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
                break
    elif node_type in ("lexical_declaration", "variable_declaration"):
        for child in node.children:
            if child.type == "variable_declarator":
                var_name = None
                is_func = False
                for sub in child.children:
                    if sub.type in ("identifier", "property_identifier"):
                        var_name = code_bytes[sub.start_byte : sub.end_byte].decode("utf-8", errors="ignore")
                    elif sub.type in ("arrow_function", "function_expression", "generator_function"):
                        is_func = True
                if var_name and is_func:
                    kind = "function"
                    name = var_name

    if name and kind:
        symbols.append(
            Symbol(
                name=name,
                kind=kind,
                path=relative_path,
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                language=language_name,
                parent=parent_name,
            )
        )

    new_parent = name if (kind == "class" and name) else parent_name
    for child in node.children:
        _traverse_ast(child, code_bytes, symbols, relative_path, language_name, new_parent)


def _js_ts_symbols(root: Path, path: Path, language: str) -> list[Symbol]:
    try:
        content = path.read_text(encoding="utf-8")
        code_bytes = content.encode("utf-8")
    except UnicodeDecodeError:
        return []
    relative = path.relative_to(root).as_posix()
    symbols: list[Symbol] = []

    try:
        import tree_sitter_javascript
        import tree_sitter_typescript
        from tree_sitter import Language, Parser

        js_lang = Language(tree_sitter_javascript.language())
        ts_lang = Language(tree_sitter_typescript.language_typescript())
        tsx_lang = Language(tree_sitter_typescript.language_tsx())

        lang = (tsx_lang if path.suffix.lower() == ".tsx" else ts_lang) if language == "typescript" else js_lang

        parser = Parser(lang)
        tree = parser.parse(code_bytes)
        _traverse_ast(tree.root_node, code_bytes, symbols, relative, language)
        if symbols:
            return sorted(symbols, key=lambda s: (s.line, s.name))
    except Exception:
        pass

    # Regex Fallback
    symbols = []
    for index, line in enumerate(content.splitlines(), start=1):
        for kind, pattern in _JS_TS_PATTERNS:
            match = pattern.match(line)
            if match:
                symbols.append(
                    Symbol(
                        name=match.group(1),
                        kind=kind,
                        path=relative,
                        line=index,
                        language=language,
                    )
                )
                break
    return symbols
