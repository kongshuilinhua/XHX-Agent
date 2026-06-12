"""记忆存储：`.xhx/memory/` 下每条事实一个 frontmatter 文件 + `MEMORY.md` 常驻索引。

frontmatter 格式（与全局 auto-memory 一致，便于人类直接读写）：

    ---
    name: <kebab-slug>
    description: <一行摘要——召回时用它做匹配>
    metadata:
      type: user | feedback | project | reference
    ---

    <正文>

设计取舍：自带一个**容错的极简 frontmatter 解析器**，不引 YAML 依赖——格式是我们自己写的、受控，
解析失败一律返回 None（绝不抛），让坏文件不至于拖垮召回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 四类事实（ROADMAP §7）：用户是谁/偏好 · 工作指导 · 进行中工作/决策 · 外部资源指针。
MEMORY_TYPES: set[str] = {"user", "feedback", "project", "reference"}

_INDEX_NAME = "MEMORY.md"
_INDEX_HEADER = "# Memory Index"
# slug 保留 ascii 词字符与 CJK，其余折成 '-'。
_SLUG_KEEP = re.compile(r"[^a-z0-9_一-鿿]+")
_INDEX_LINE = re.compile(r"\]\(([^)]+)\)")


@dataclass
class MemoryRecord:
    """一条记忆事实。`path` 为落盘文件路径（内存构造时可为 None）。"""

    name: str
    description: str
    mtype: str
    body: str
    path: Path | None = None


def memory_dir(workspace: Path) -> Path:
    """记忆目录：`<workspace>/.xhx/memory/`。"""
    return Path(workspace) / ".xhx" / "memory"


def slugify(text: str) -> str:
    """把任意标题折成文件名安全的 kebab-slug（保留 CJK）；空则回退 'memory'。"""
    lowered = (text or "").strip().lower()
    slug = _SLUG_KEEP.sub("-", lowered).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "memory"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """解析 `---` 围起来的 frontmatter，返回 (扁平化字段, 正文)；不是合法 frontmatter 返回 None。

    支持一层嵌套：`metadata:` 下缩进的 `type:` 会被收成 `type` 键。
    """
    if not text.startswith("---"):
        return None
    # 去掉首个 '---' 行后，找下一条单独的 '---' 作为闭合。
    rest = text.split("\n", 1)
    if len(rest) < 2:
        return None
    after = rest[1]
    end = after.find("\n---")
    if end == -1:
        return None
    front_block = after[:end]
    body = after[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]

    fields: dict[str, str] = {}
    in_metadata = False
    for raw in front_block.splitlines():
        if not raw.strip():
            continue
        indented = raw[0] in (" ", "\t")
        key, sep, value = raw.strip().partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "metadata" and not value:
            in_metadata = True
            continue
        if in_metadata and indented:
            # metadata 下的子键（主要是 type）
            fields[key] = value
            continue
        in_metadata = False
        fields[key] = value
    return fields, body.strip("\n")


def parse_memory_file(path: Path) -> MemoryRecord | None:
    """解析单个记忆文件为 MemoryRecord；任何异常/缺字段都返回 None（不抛）。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    parsed = _parse_frontmatter(text)
    if parsed is None:
        return None
    fields, body = parsed
    name = fields.get("name") or Path(path).stem
    description = fields.get("description", "")
    mtype = fields.get("type", "project")
    if mtype not in MEMORY_TYPES:
        mtype = "project"
    return MemoryRecord(name=name, description=description, mtype=mtype, body=body, path=Path(path))


def _render_memory_file(record: MemoryRecord) -> str:
    """把 MemoryRecord 渲染成 frontmatter 文本。"""
    return (
        "---\n"
        f"name: {record.name}\n"
        f"description: {record.description}\n"
        "metadata:\n"
        f"  type: {record.mtype}\n"
        "---\n\n"
        f"{record.body.strip()}\n"
    )


def _index_line(record: MemoryRecord, filename: str) -> str:
    desc = record.description.strip() or record.name
    return f"- [{record.name}]({filename}) — {desc}"


def _update_index(directory: Path, filename: str, record: MemoryRecord) -> None:
    """在 `MEMORY.md` 里 upsert 一行指向 `filename` 的索引（同文件名去重覆盖）。"""
    index = directory / _INDEX_NAME
    lines: list[str] = []
    if index.exists():
        lines = index.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != _INDEX_HEADER:
        lines = [_INDEX_HEADER, ""] + [ln for ln in lines if ln.strip() != _INDEX_HEADER]
    kept = [ln for ln in lines if not _line_points_to(ln, filename)]
    kept.append(_index_line(record, filename))
    # 规整：表头 + 空行 + 去空的条目行。
    entries = [ln for ln in kept[1:] if ln.strip()]
    out = [_INDEX_HEADER, ""] + entries
    index.write_text("\n".join(out) + "\n", encoding="utf-8")


def _line_points_to(line: str, filename: str) -> bool:
    match = _INDEX_LINE.search(line)
    return bool(match) and match.group(1) == filename


def write_memory(
    workspace: Path,
    *,
    name: str,
    description: str,
    mtype: str,
    body: str,
) -> Path:
    """写入一条记忆（落盘 frontmatter 文件 + 更新 `MEMORY.md`）。同 name(slug) 覆盖。

    非法 `mtype` 抛 ValueError。返回写入的文件路径。
    """
    if mtype not in MEMORY_TYPES:
        raise ValueError(f"Unknown memory type {mtype!r}. Allowed: {sorted(MEMORY_TYPES)}.")
    directory = memory_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    filename = f"{slug}.md"
    record = MemoryRecord(name=name, description=description, mtype=mtype, body=body, path=directory / filename)
    record.path.write_text(_render_memory_file(record), encoding="utf-8")
    _update_index(directory, filename, record)
    return record.path


def list_memories(workspace: Path) -> list[MemoryRecord]:
    """列出全部记忆（跳过 `MEMORY.md` 与解析失败的文件），按 name 稳定排序。"""
    directory = memory_dir(workspace)
    if not directory.exists():
        return []
    records: list[MemoryRecord] = []
    for path in sorted(directory.glob("*.md")):
        if path.name == _INDEX_NAME:
            continue
        record = parse_memory_file(path)
        if record is not None:
            records.append(record)
    return sorted(records, key=lambda r: r.name)


def delete_memory(workspace: Path, name: str) -> bool:
    """删除一条记忆（文件 + 索引行）。文件原本存在返回 True，否则 False。"""
    directory = memory_dir(workspace)
    filename = f"{slugify(name)}.md"
    target = directory / filename
    existed = target.exists()
    if existed:
        target.unlink()
    index = directory / _INDEX_NAME
    if index.exists():
        lines = index.read_text(encoding="utf-8").splitlines()
        entries = [ln for ln in lines[1:] if ln.strip() and not _line_points_to(ln, filename)]
        index.write_text("\n".join([_INDEX_HEADER, ""] + entries) + "\n", encoding="utf-8")
    return existed
