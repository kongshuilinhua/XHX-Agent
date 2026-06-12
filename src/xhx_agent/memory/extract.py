"""记忆自动抽取（suggest-confirm，Phase 6b）：跑完后让 LLM 提议耐久跨会话事实，用户确认后才落盘。

严格输出格式 + 严格解析 = 既让真模型能提议，又保证 mock/无关输出**确定性解析为空**（防误报、防污染）。
确认交互由调用方（REPL）负责——本模块只产出候选，**绝不自动写盘**。
"""

from __future__ import annotations

from typing import Any

from xhx_agent.memory.store import MEMORY_TYPES, MemoryRecord, slugify

_MARKER = "MEMORY"
_MAX_CANDIDATES = 3

EXTRACTION_SYSTEM_PROMPT = (
    "You review a FINISHED coding-agent session and decide what — if anything — is worth remembering "
    "ACROSS future sessions. Remember only durable facts of these four kinds: "
    "user (who the user is / stable preferences), feedback (how the user wants you to work, with the why), "
    "project (ongoing goals or decisions not derivable from code/git), reference (pointers to external resources). "
    "Do NOT remember anything derivable from the code, git history, or this one task; no transient state; "
    "no bug-fix recipes. Be conservative — most sessions yield nothing.\n"
    "If nothing qualifies, output exactly: NONE\n"
    "Otherwise output ONE LINE per fact and nothing else, in EXACTLY this format:\n"
    "MEMORY | type=<user|feedback|project|reference> | name=<short kebab title> | desc=<one line> | body=<the fact>"
)


def _parse_fields(line: str) -> dict[str, str]:
    """解析一行 `MEMORY | k=v | k=v | body=...`；body= 之后整段作为正文（允许含 | 和 =）。"""
    body = ""
    head = line
    lower = line.lower()
    idx = lower.find("body=")
    if idx != -1:
        body = line[idx + len("body=") :].strip()
        head = line[:idx]
    fields: dict[str, str] = {}
    for chunk in head.split("|"):
        key, sep, value = chunk.partition("=")
        if sep:
            fields[key.strip().lower()] = value.strip()
    if body:
        fields["body"] = body
    return fields


def parse_memory_candidates(
    text: str,
    *,
    existing_names: set[str] | None = None,
    limit: int = _MAX_CANDIDATES,
) -> list[MemoryRecord]:
    """从模型输出严格解析候选记忆；不匹配格式 / NONE → []。对已有 name(slug) 去重，截断到 limit。"""
    existing = set(existing_names or set())
    out: list[MemoryRecord] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or not line.upper().startswith(_MARKER):
            continue
        fields = _parse_fields(line)
        mtype = fields.get("type", "")
        name = fields.get("name", "").strip()
        if mtype not in MEMORY_TYPES or not name:
            continue
        slug = slugify(name)
        if slug in existing:
            continue
        description = fields.get("desc", "").strip()
        body = fields.get("body", "").strip() or description
        out.append(MemoryRecord(name=name, description=description, mtype=mtype, body=body))
        existing.add(slug)
        if len(out) >= limit:
            break
    return out


def propose_memories(
    client: Any,
    task: str,
    transcript: str,
    *,
    existing_names: set[str] | None = None,
    limit: int = _MAX_CANDIDATES,
) -> list[MemoryRecord]:
    """调 LLM 提议候选记忆（**不写盘**）。client.chat(messages, []) → .content；解析为候选列表。"""
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"Task:\n{task}\n\nSession outcome:\n{transcript}".strip()},
    ]
    result = client.chat(messages, [])
    return parse_memory_candidates(getattr(result, "content", None) or "", existing_names=existing_names, limit=limit)
