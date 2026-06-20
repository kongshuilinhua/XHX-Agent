"""filehistory/history.py 单测：编辑追踪、快照、回退。"""

from __future__ import annotations

from pathlib import Path

from xhx_agent.filehistory.history import FileHistory


def test_snapshot_and_rewind(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("v1", encoding="utf-8")

    fh = FileHistory(str(tmp_path), "sess1")
    assert fh.has_snapshots() is False

    # 记录初始版本并快照
    fh.track_edit(str(target))
    fh.make_snapshot(0, "初始")
    assert fh.has_snapshots() is True
    assert len(fh.get_snapshots()) == 1

    # 修改文件后再快照
    target.write_text("v2-changed", encoding="utf-8")
    fh.track_edit(str(target))
    fh.make_snapshot(1, "改了")
    assert len(fh.get_snapshots()) == 2

    # 回退到快照 0 → 文件恢复为 v1
    changed = fh.rewind(0)
    assert str(target.resolve()) in [str(Path(c)) for c in changed] or changed
    assert target.read_text(encoding="utf-8") == "v1"
    # 回退后快照被截断到目标
    assert len(fh.get_snapshots()) == 1


def test_rewind_out_of_range(tmp_path: Path) -> None:
    fh = FileHistory(str(tmp_path), "sess2")
    assert fh.rewind(0) == []
    assert fh.rewind(-1) == []


def test_track_edit_missing_file_noop(tmp_path: Path) -> None:
    fh = FileHistory(str(tmp_path), "sess3")
    # 不存在的文件不报错
    fh.track_edit(str(tmp_path / "ghost.py"))
    fh.make_snapshot(0, "x")
    assert fh.has_snapshots() is True
