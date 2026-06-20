"""小工具单测：EditFile / FileStateCache / Grep / XhxCompleter。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xhx_agent.cli.completion import XhxCompleter
from xhx_agent.tools.edit_file import EditFile
from xhx_agent.tools.edit_file import Params as EditParams
from xhx_agent.tools.file_state_cache import FileStateCache
from xhx_agent.tools.grep import Grep
from xhx_agent.tools.grep import Params as GrepParams

# --- EditFile ---


def _edit(tool: EditFile, **kw):
    return asyncio.run(tool.execute(EditParams(**kw)))


def test_edit_file_success(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("hello world\n", encoding="utf-8")
    res = _edit(EditFile(), file_path=str(f), old_string="world", new_string="there")
    assert res.is_error is False
    assert f.read_text(encoding="utf-8") == "hello there\n"


def test_edit_file_not_found(tmp_path: Path) -> None:
    res = _edit(EditFile(), file_path=str(tmp_path / "ghost.py"), old_string="x", new_string="y")
    assert res.is_error and "not found" in res.output


def test_edit_file_missing_and_ambiguous(tmp_path: Path) -> None:
    f = tmp_path / "b.py"
    f.write_text("a a a\n", encoding="utf-8")
    miss = _edit(EditFile(), file_path=str(f), old_string="zzz", new_string="q")
    assert miss.is_error and "not found" in miss.output
    ambi = _edit(EditFile(), file_path=str(f), old_string="a", new_string="q")
    assert ambi.is_error and "unique" in ambi.output


# --- FileStateCache ---


def test_file_state_cache(tmp_path: Path) -> None:
    f = tmp_path / "c.py"
    f.write_text("v1", encoding="utf-8")
    cache = FileStateCache()
    # 未读 → 不允许编辑
    ok, msg = cache.check(str(f))
    assert ok is False and "has not been read" in msg
    # 记录后 → 允许
    cache.record(str(f), "v1", f.stat().st_mtime_ns)
    ok2, _ = cache.check(str(f))
    assert ok2 is True
    # 外部修改 mtime → 检测到不一致
    import os
    import time

    time.sleep(0.01)
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 1_000_000))
    ok3, msg3 = cache.check(str(f))
    assert ok3 is False and "modified since" in msg3
    # update 后重新一致
    cache.update(str(f))
    ok4, _ = cache.check(str(f))
    assert ok4 is True


def test_edit_with_state_cache_gate(tmp_path: Path) -> None:
    f = tmp_path / "d.py"
    f.write_text("foo bar", encoding="utf-8")
    cache = FileStateCache()
    tool = EditFile(file_state_cache=cache)
    # 未读 → 被拦
    blocked = _edit(tool, file_path=str(f), old_string="foo", new_string="baz")
    assert blocked.is_error and "has not been read" in blocked.output
    # 记录后可编辑
    cache.record(str(f.resolve()), "foo bar", f.stat().st_mtime_ns)
    ok = _edit(tool, file_path=str(f), old_string="foo", new_string="baz")
    assert ok.is_error is False


# --- Grep ---


def _grep(**kw):
    return asyncio.run(Grep().execute(GrepParams(**kw)))


def test_grep_matches(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("alpha\nNEEDLE here\nbeta\n", encoding="utf-8")
    res = _grep(pattern="NEEDLE", path=str(tmp_path))
    assert "NEEDLE here" in res.output


def test_grep_no_match_and_errors(tmp_path: Path) -> None:
    (tmp_path / "y.py").write_text("nothing\n", encoding="utf-8")
    assert "No matches" in _grep(pattern="zzz", path=str(tmp_path)).output
    assert _grep(pattern="x", path=str(tmp_path / "missing")).is_error
    assert _grep(pattern="(unclosed", path=str(tmp_path)).is_error  # 非法正则


def test_grep_include_filter(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("match1\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("match1\n", encoding="utf-8")
    res = _grep(pattern="match1", path=str(tmp_path), include="*.py")
    assert "a.py" in res.output and "a.txt" not in res.output


# --- XhxCompleter ---


def test_completer_slash_and_path(tmp_path: Path) -> None:
    comp = XhxCompleter(tmp_path)
    assert comp.get_completions("") == []
    slash = comp.get_completions("/he")
    assert "/help" in slash
    # 命令 + 参数 → 路径补全
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    arg = comp.get_completions("/review notes")
    assert any("notes.md" in c for c in arg)
    # 路径前缀补全
    paths = comp.get_completions("notes")
    assert any("notes.md" in p for p in paths)
