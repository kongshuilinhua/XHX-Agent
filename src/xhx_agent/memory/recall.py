"""确定性召回：把任务文本与记忆做 token 重叠打分，取 top-N。无 LLM 调用、可复现。

匹配偏向 `description`+`name`（权重 2）高于正文（权重 1）。中文按**单字**切 token 以支持重叠。
生命周期防腐烂：召回时若记忆正文点名的相对代码文件已不存在，则跳过该条（`verify=True`）。
召回结果可渲染成一段 markdown，注入 context-pack 或编排器 system prompt（无命中返回 ""）。
"""

from __future__ import annotations

import re
from pathlib import Path

from xhx_agent.memory.store import MemoryRecord, list_memories

_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[一-鿿]")
_BACKTICK = re.compile(r"`([^`]+)`")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".c", ".h", ".cpp",
    ".md", ".json", ".toml", ".yaml", ".yml", ".txt", ".cfg", ".ini", ".sh", ".sql",
}
_MAX_BODY_CHARS = 280


def _tokens(text: str) -> set[str]:
    """ascii 词 token + CJK 单字 token（小写、去重）。"""
    lowered = (text or "").lower()
    toks: set[str] = set(_WORD.findall(lowered))
    toks.update(_CJK.findall(lowered))
    return toks


def _score(query_tokens: set[str], record: MemoryRecord) -> int:
    """description+name 命中权重 2、正文命中权重 1，累加得分。"""
    strong = _tokens(f"{record.description} {record.name}")
    weak = _tokens(record.body)
    score = 0
    for token in query_tokens:
        if token in strong:
            score += 2
        elif token in weak:
            score += 1
    return score


def _looks_like_filepath(raw: str) -> str | None:
    """raw 形似**相对**代码文件路径（含分隔符 + 已知扩展名、非 URL/绝对路径）则返回规整后的路径，否则 None。"""
    token = raw.strip().strip("`").rstrip("/.,;:)»")
    if not token or "://" in token or token.startswith("/") or _DRIVE.match(token):
        return None
    if "/" not in token and "\\" not in token:
        return None
    if Path(token.replace("\\", "/")).suffix.lower() not in _CODE_EXT:
        return None
    return token.replace("\\", "/")


def _record_is_fresh(record: MemoryRecord, workspace: Path) -> bool:
    """记忆正文点名的相对代码文件若已失踪，判为陈旧（不新鲜）。"""
    body = record.body
    candidates = _BACKTICK.findall(body) + body.split()
    for raw in candidates:
        rel = _looks_like_filepath(raw)
        if rel and not (Path(workspace) / rel).exists():
            return False
    return True


def recall_memories(
    workspace: Path,
    query: str,
    *,
    limit: int = 5,
    verify: bool = True,
) -> list[MemoryRecord]:
    """按确定性 token 重叠召回与 query 相关的记忆（得分降序、name 升序稳定）。

    `verify=True` 时跳过正文点名文件已失踪的陈旧记忆。无命中返回 []。
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    scored = [(record, _score(query_tokens, record)) for record in list_memories(workspace)]
    scored = [(record, points) for record, points in scored if points > 0]
    scored.sort(key=lambda item: (-item[1], item[0].name))

    out: list[MemoryRecord] = []
    for record, _points in scored:
        if verify and not _record_is_fresh(record, workspace):
            continue
        out.append(record)
        if len(out) >= limit:
            break
    return out


def render_recalled_memories(workspace: Path, query: str, *, limit: int = 5) -> str:
    """召回并渲染成可注入 system prompt 的 markdown 块；无命中返回 ""（对现有行为零影响）。"""
    records = recall_memories(workspace, query, limit=limit)
    if not records:
        return ""
    lines = [
        "\n\n## Recalled memory (cross-session facts)",
        "Facts remembered from earlier sessions. They reflect what was true when written — "
        "verify against current code before relying on them.",
    ]
    for record in records:
        body = " ".join(record.body.split())
        if len(body) > _MAX_BODY_CHARS:
            body = body[: _MAX_BODY_CHARS - 3].rstrip() + "..."
        lines.append(f"- [{record.mtype}] {record.description or record.name}")
        if body:
            lines.append(f"  {body}")
    return "\n".join(lines)
