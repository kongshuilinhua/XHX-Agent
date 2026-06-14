"""子 agent（dispatch）：父 loop/plan 把聚焦探索任务委派给隔离子循环。

子 agent 有**自己的消息历史**、**受限工具**（MVP 只读 explore=search/read_file）、**限定轮数**，跑完
只把浓缩结论回给父——父上下文只长一句。子 agent 的工具调用仍走 kernel（策略门控 + 证据 trace 不变）。
并行多子 agent / 写型 worktree 子 agent 留后续切片。
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from xhx_agent.models import build_chat_client
from xhx_agent.models.routing import build_routed_client
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import emit_event

if TYPE_CHECKING:
    from xhx_agent.orchestrators.base import OrchestratorContext

# agent_type → 允许的工具集。explore=只读；edit=可写（在隔离 worktree 里）。
AGENT_TOOLSETS: dict[str, set[str]] = {
    "explore": {"search", "read_file"},
    "edit": {"search", "read_file", "apply_patch"},
}
# 写型 agent_type：跑在自己的 git worktree 里、改完串行合并回父工作区。
WRITE_AGENT_TYPES: set[str] = {"edit"}
SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused sub-agent dispatched by a parent coding agent. Use ONLY the provided read-only "
    "tools to investigate, then reply with a concise, self-contained conclusion (a few sentences). "
    "Do not attempt to write files. Stop as soon as you can answer."
)
WRITE_SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused WRITE sub-agent dispatched by a parent coding agent, working in an ISOLATED copy of "
    "the repository. Accomplish ONLY the assigned sub-task: read what you need, then make every edit with "
    "apply_patch (relative paths, unified diff). When the sub-task is done, reply with a one-line summary "
    "and no tool calls. Keep your changes minimal and scoped to the sub-task."
)


def run_subagent(
    ctx: OrchestratorContext,
    *,
    description: str,
    prompt: str,
    agent_type: str = "explore",
    turn: int = 0,
) -> str:
    """跑一个隔离只读子循环，返回浓缩结论（作为父的 dispatch 工具结果）。"""
    from xhx_agent.orchestrators._toolturn import _execute_tool_call_rich, chat_and_count

    allowed = AGENT_TOOLSETS.get(agent_type)
    if allowed is None:
        return f"[dispatch] unknown agent_type '{agent_type}'. Supported: {sorted(AGENT_TOOLSETS)}."

    client = build_routed_client(
        ctx.original_workspace,
        role="explore",
        base_profile_name=ctx.profile.name,
        event_callback=ctx.event_callback,
        build_client_func=build_chat_client,
    )
    schemas = [s for s in ctx.kernel.tool_registry.tool_schemas() if s["function"]["name"] in allowed]
    messages: list[dict] = [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    emit_event(
        ctx.event_callback, "subagent_start", f"dispatch[{agent_type}]: {description or prompt[:60]}",
        turn=turn, agent_type=agent_type,
    )

    answer = ""
    max_turns = load_config(ctx.original_workspace).max_subagent_turns
    for _ in range(max_turns):
        result = chat_and_count(ctx, client, messages, schemas, turn=turn)
        if not result.tool_calls:
            answer = result.content or ""
            break
        messages.append({
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in result.tool_calls
            ],
        })
        for tc in result.tool_calls:
            if tc.name not in allowed:
                content = f"[dispatch] tool '{tc.name}' is not allowed for a '{agent_type}' sub-agent."
            else:
                _tc, content, _changed, _meta = _execute_tool_call_rich(ctx, tc, turn)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:4000]})
    else:
        answer = answer or "Sub-agent reached its turn limit without a firm conclusion."

    emit_event(ctx.event_callback, "subagent_done", "Sub-agent finished.", turn=turn, agent_type=agent_type)
    return f"[sub-agent {agent_type}] {answer}".strip()


def run_write_subagent(
    ctx: OrchestratorContext,
    *,
    description: str,
    prompt: str,
    turn: int = 0,
    seed_files: list[str] | None = None,
) -> tuple[str, list[str]]:
    """跑一个隔离【可写】子循环（自己的 git worktree），改完**串行合并回父工作区**（冲突先到先得）。

    返回 (浓缩结论, 已合并进父工作区的文件列表)。父工作区 = ctx.tool_context.workspace。
    非 git 仓库时降级为就地执行（无隔离，但仍记 claim 做冲突检测）。
    """
    from xhx_agent.safety.worktree import WorktreeContext

    allowed = AGENT_TOOLSETS["edit"]
    label = (description or prompt[:40] or "edit").strip()
    sub_run_id = f"{ctx.run_id}-edit{turn}-{uuid.uuid4().hex[:8]}"
    emit_event(ctx.event_callback, "subagent_start", f"dispatch[edit]: {label}", turn=turn, agent_type="edit")

    wt = WorktreeContext(ctx.original_workspace, sub_run_id)
    with ctx.subagent_lock:          # ① 串行化 worktree 创建（git 锁争用）
        wt.__enter__()
    try:
        run_ctx = ctx
        if wt.is_active:
            sub_tool_context = ctx.tool_context.model_copy(update={"workspace": wt.active_path})
            run_ctx = dataclasses.replace(ctx, tool_context=sub_tool_context)
            _seed_worktree(ctx, wt.active_path, seed_files)   # 播种：让本轮 edit 看得到前序已改文件
        answer, changed = _drive_write_loop(run_ctx, prompt, allowed, turn)   # 锁外并行（各自 worktree）
        merge_root = wt.active_path if wt.is_active else ctx.tool_context.workspace
        with ctx.subagent_lock:      # ② 串行化合并（claims + 文件拷贝）
            applied, conflicts = _merge_into_parent(ctx, merge_root, changed, label)
    finally:
        with ctx.subagent_lock:      # ③ 串行化 worktree 清理（git 锁）
            wt.__exit__(None, None, None)

    parts = [f"[sub-agent edit] {answer or 'edit sub-agent finished.'}"]
    if applied:
        parts.append(f"merged {len(applied)} file(s): {', '.join(sorted(set(applied)))}")
    if conflicts:
        parts.append(
            "CONFLICT on "
            f"{len(conflicts)} file(s) — kept the earlier sub-agent's version: {', '.join(sorted(set(conflicts)))}"
        )
    emit_event(
        ctx.event_callback, "subagent_done",
        f"edit sub-agent: merged {len(applied)} file(s), {len(conflicts)} conflict(s).",
        turn=turn, agent_type="edit", merged=sorted(set(applied)), conflicts=sorted(set(conflicts)),
    )
    return " | ".join(parts), sorted(set(applied))


def _drive_write_loop(ctx: OrchestratorContext, prompt: str, allowed: set[str], turn: int) -> tuple[str, list[str]]:
    """写型子循环：受限 tool-calling（含 apply_patch），收集本子 agent 改动的相对路径。"""
    from xhx_agent.orchestrators._toolturn import _execute_tool_call_rich, chat_and_count

    client = build_routed_client(
        ctx.original_workspace, role="edit", base_profile_name=ctx.profile.name,
        event_callback=ctx.event_callback, build_client_func=build_chat_client,
    )
    schemas = [s for s in ctx.kernel.tool_registry.tool_schemas() if s["function"]["name"] in allowed]
    messages: list[dict] = [
        {"role": "system", "content": WRITE_SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    answer = ""
    changed: list[str] = []
    max_turns = load_config(ctx.original_workspace).max_subagent_turns
    for _ in range(max_turns):
        result = chat_and_count(ctx, client, messages, schemas, turn=turn)
        if not result.tool_calls:
            answer = result.content or ""
            break
        messages.append({
            "role": "assistant", "content": result.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in result.tool_calls
            ],
        })
        for tc in result.tool_calls:
            if tc.name not in allowed:
                content = f"[dispatch] tool '{tc.name}' is not allowed for this sub-agent."
            else:
                _tc, content, ch, _meta = _execute_tool_call_rich(ctx, tc, turn)
                changed.extend(ch)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:4000]})
    else:
        answer = answer or "Edit sub-agent reached its turn limit."
    return answer, changed


def _merge_into_parent(
    ctx: OrchestratorContext, merge_root, changed_rel: list[str], label: str
) -> tuple[list[str], list[str]]:
    """串行把 merge_root 里改动的文件合并进父工作区（ctx.tool_context.workspace）；同文件被别的子 agent 占用即冲突。"""
    import shutil

    target = ctx.tool_context.workspace
    applied: list[str] = []
    conflicts: list[str] = []
    for rel in dict.fromkeys(changed_rel):  # 去重保序
        if not rel:
            continue
        owner = ctx.subagent_claims.get(rel)
        if owner is not None and owner != label:
            conflicts.append(rel)
            continue
        src = merge_root / rel
        dest = target / rel
        if merge_root != target and src.exists() and src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        ctx.subagent_claims[rel] = label
        applied.append(rel)
    return applied, conflicts


def _seed_worktree(ctx: OrchestratorContext, worktree_root: Path, seed_files: list[str] | None) -> None:
    """把父工作区里\"前序已改文件\"拷进新建 worktree，使后续 edit 在其之上继续改（解决 worktree 从 HEAD 切出看不到未提交改动的问题）。"""
    import shutil
    if not seed_files:
        return
    parent = ctx.tool_context.workspace
    for rel in dict.fromkeys(seed_files):
        if not rel:
            continue
        src = parent / rel
        dest = worktree_root / rel
        if src.exists() and src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
