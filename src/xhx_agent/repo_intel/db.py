"""SQLite 镜像：把 JSON 主索引同步成一份 .xhx/repo/index.db（symbols/imports/references/calls 四表）。

注意（回看勿误解）：当前查询路径仍走 JSON 加载的内存索引，这份 SQLite 是「只写镜像」——
每次全量重刷（DELETE + INSERT），作为未来直接 SQL 查询的预留，目前没有读取它的代码。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from xhx_agent.repo_intel.types import RepoIntelIndex


def repo_db_path(workspace: Path) -> Path:
    return workspace / ".xhx" / "repo" / "index.db"


def sync_index_to_sqlite(workspace: Path, index: RepoIntelIndex) -> Path:
    """把内存索引全量写入 SQLite 镜像：建表 → 清空旧记录 → 批量插入 symbols/imports/references/calls。"""
    db_file = repo_db_path(workspace)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file))
    try:
        cursor = conn.cursor()

        # 1. Create tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                name TEXT,
                kind TEXT,
                path TEXT,
                line INTEGER,
                end_line INTEGER,
                language TEXT,
                parent TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols (path)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS imports (
                importer TEXT,
                target TEXT,
                kind TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_imports_importer ON imports (importer)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_imports_target ON imports (target)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS "references" (
                name TEXT,
                path TEXT,
                line INTEGER,
                excerpt TEXT
            )
        """)
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_references_name ON "references" (name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_references_path ON "references" (path)')

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                caller TEXT,
                caller_path TEXT,
                caller_line INTEGER,
                callee TEXT,
                callee_path TEXT,
                callee_line INTEGER,
                call_line INTEGER,
                language TEXT,
                confidence REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls (caller)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls (callee)")

        # 2. 全量重刷：每次同步清空再插入（这是只写镜像，不做增量）。
        cursor.execute("DELETE FROM symbols")
        cursor.execute("DELETE FROM imports")
        cursor.execute('DELETE FROM "references"')
        cursor.execute("DELETE FROM calls")

        # 3. Insert Symbols
        symbols_data = [
            (
                s.name,
                s.kind,
                s.path,
                s.line,
                s.end_line,
                s.language,
                s.parent,
            )
            for s in index.symbol_index.symbols
        ]
        if symbols_data:
            cursor.executemany("INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?)", symbols_data)

        # 4. Insert Imports
        imports_data = [(e.importer, e.target, e.kind) for e in index.import_graph.edges]
        if imports_data:
            cursor.executemany("INSERT INTO imports VALUES (?, ?, ?)", imports_data)

        # 5. Insert References
        references_data = [(r.name, r.path, r.line, r.excerpt) for r in index.reference_index.references]
        if references_data:
            cursor.executemany('INSERT INTO "references" VALUES (?, ?, ?, ?)', references_data)

        # 6. Insert Calls
        calls_data = [
            (
                c.caller,
                c.caller_path,
                c.caller_line,
                c.callee,
                c.callee_path,
                c.callee_line,
                c.call_line,
                c.language,
                c.confidence,
            )
            for c in index.call_graph.edges
        ]
        if calls_data:
            cursor.executemany("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", calls_data)

        conn.commit()
    finally:
        conn.close()

    return db_file
