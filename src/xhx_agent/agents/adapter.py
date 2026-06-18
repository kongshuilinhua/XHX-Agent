"""Agent 适配器：桥接新 Agent 基础设施到现有 RuntimeApp。

RuntimeApp 仍负责 worktree / MCP / evidence / scan 等初始化，
主执行循环使用 XHX-Agent 现有的 tool-calling 协议。
同时发射 RuntimeEvent 事件，驱动 TUI ConsoleState 的状态显示。
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from xhx_agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from xhx_agent.models.async_wrapper import AsyncClientWrapper
    from xhx_agent.permissions import PermissionChecker

log = logging.getLogger(__name__)


def _emit(callback: Any, event_type: str, message: str = "", **payload: Any) -> None:
    """安全发射 RuntimeEvent：callback 为 None 时静默跳过。"""
    if callback is None:
        return
    try:
        from xhx_agent.runtime.events import emit_event
        emit_event(callback, event_type, message, **payload)
    except Exception:
        pass


def run_agent_sync(
    task: str,
    *,
    client: "AsyncClientWrapper",
    registry: ToolRegistry,
    protocol: str = "openai-compat",
    work_dir: str = ".",
    max_iterations: int = 50,
    permission_checker: "PermissionChecker | None" = None,
    context_window: int = 200_000,
    instructions_content: str = "",
    event_callback: Any = None,
    **kwargs: Any,
) -> tuple[str, int]:
    """使用 ReAct 循环运行 Agent，返回 (响应文本, 轮数)。

    循环：chat → 解析 tool_calls → 执行工具 → 回填结果 → 继续。
    全程发射 RuntimeEvent 驱动 TUI 状态显示。
    """
    import asyncio
    from pathlib import Path

    from xhx_agent.context.compaction import compact_messages
    from xhx_agent.context.compiler import _estimate_tokens

    messages: list[dict[str, Any]] = []

    # System prompt
    sys_prompt = _build_default_system_prompt(work_dir, instructions_content)
    if sys_prompt:
        messages.append({"role": "system", "content": sys_prompt})

    messages.append({"role": "user", "content": task})

    tool_schemas = registry.get_all_schemas(protocol)
    answer_text = ""
    consecutive_errors = 0
    cumulative_tokens = 0
    ws = Path(work_dir)

    for turn in range(1, max_iterations + 1):
        # 发射 turn 开始
        _emit(event_callback, "context_pack",
              turn=turn,
              selected=len(messages),
              omitted=0,
              used_tokens_estimate=sum(
                  _estimate_tokens(str(m.get("content", "")))
                  for m in messages if m.get("role") != "system"
              ),
              budget_tokens=context_window)

        # ---- 压缩检查 ----
        est_tokens = sum(
            _estimate_tokens(str(m.get("content", "")))
            for m in messages if m.get("role") != "system"
        )
        if est_tokens > 12_000:
            before = len(messages)
            try:
                threshold, keep = (8_000, 2_000)
                if context_window > 0:
                    reserve = min(context_window // 4, 16_000)
                    threshold = max(context_window - reserve - 1_000, 4_000)
                    keep = min(context_window // 3, 24_000)

                async def _summarize(text: str) -> str:
                    stream = client.stream(
                        [{"role": "user", "content": text}],
                        system="Summarize the following conversation concisely.",
                    )
                    text_out = ""
                    async for event in stream:
                        from xhx_agent.tools.base import StreamEnd, TextDelta
                        if isinstance(event, TextDelta):
                            text_out += event.text
                        elif isinstance(event, StreamEnd):
                            break
                    return text_out

                sync_summarize = lambda t: asyncio.run(_summarize(t))
                messages = compact_messages(
                    messages, sync_summarize,
                    max_tokens=threshold, keep_recent_tokens=keep,
                )
                after = len(messages)
                _emit(event_callback, "compaction",
                      before=before, after=after)
            except Exception:
                pass

        # ---- 调用 LLM ----
        resp_text = ""
        resp_tool_calls: list[dict[str, Any]] = []
        call_start = time.time()

        try:
            async def _chat() -> None:
                nonlocal resp_text, resp_tool_calls
                stream = client.stream(messages, tools=tool_schemas)
                async for event in stream:
                    from xhx_agent.tools.base import (
                        StreamEnd, TextDelta, ToolCallComplete,
                    )
                    if isinstance(event, TextDelta):
                        resp_text += event.text
                        _emit(event_callback, "model_delta",
                              turn=turn, delta=event.text)
                    elif isinstance(event, ToolCallComplete):
                        args = event.arguments
                        if isinstance(args, dict):
                            args = json.dumps(args, ensure_ascii=False)
                        resp_tool_calls.append({
                            "id": event.tool_id,
                            "type": "function",
                            "function": {
                                "name": event.tool_name,
                                "arguments": args,
                            },
                        })
                        _emit(event_callback, "tool_start",
                              tool=event.tool_name, turn=turn,
                              arguments=event.arguments)
                    elif isinstance(event, StreamEnd):
                        nonlocal cumulative_tokens
                        cumulative_tokens += event.input_tokens + event.output_tokens
                        _emit(event_callback, "token_usage",
                              prompt=event.input_tokens,
                              completion=event.output_tokens,
                              cumulative_total=cumulative_tokens,
                              duration_ms=int((time.time() - call_start) * 1000))

            asyncio.run(_chat())
        except Exception as e:
            log.error("LLM call failed: %s", e)
            consecutive_errors += 1
            _emit(event_callback, "error", str(e))
            if consecutive_errors >= 3:
                break
            continue

        consecutive_errors = 0

        # 没有工具调用 → 完成
        if not resp_tool_calls:
            answer_text = resp_text
            break

        # 记录 assistant 消息
        messages.append({
            "role": "assistant",
            "content": resp_text or "",
            "tool_calls": resp_tool_calls,
        })

        # ---- 执行工具 ----
        tool_messages = []
        for tc in resp_tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            tool_args = fn.get("arguments", {})
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except json.JSONDecodeError:
                    tool_args = {}

            tool_start = time.time()
            tool_result_text = _execute_tool(
                registry, tool_name, tool_args, ws,
            )

            # 发射工具结果
            _emit(event_callback, "tool_result",
                  tool=tool_name, turn=turn,
                  status="success",
                  summary=tool_result_text[:200],
                  duration_ms=int((time.time() - tool_start) * 1000))

            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": tool_result_text[:8000],
            })

        messages.extend(tool_messages)

    return answer_text, turn


def _execute_tool(
    registry: ToolRegistry,
    tool_name: str,
    tool_args: dict[str, Any],
    workspace: Path,
) -> str:
    """执行单个工具调用，返回文本结果。兼容新旧两种工具格式。"""
    tool = registry.get(tool_name)
    if tool is None:
        return f"Unknown tool: {tool_name}"

    # 新 Tool ABC 格式
    if hasattr(tool, "execute"):
        import asyncio
        try:
            params = tool.params_model(**tool_args) if tool_args else tool.params_model()
            result = asyncio.run(tool.execute(params))
            text = result.output
            return f"Error: {text}" if result.is_error else text
        except Exception as e:
            return f"Tool execution failed: {e}"

    # 旧 ToolDefinition 格式
    if hasattr(tool, "runner") and tool.runner is not None:
        try:
            from xhx_agent.tools.registry import ToolContext
            ctx = ToolContext(workspace=workspace)
            result = tool.runner(ctx, tool_args)
            payload = result.trace_payload if hasattr(result, "trace_payload") else {}
            if isinstance(payload, dict):
                if "content" in payload:
                    return str(payload["content"])
                if "results" in payload:
                    return str(payload["results"])
            if result.error:
                return f"Error: {result.error}"
            return result.summary
        except Exception as e:
            return f"Tool execution failed: {e}"

    return f"Cannot execute tool: {tool_name}"


def _build_default_system_prompt(work_dir: str, instructions: str) -> str:
    import platform
    from datetime import date

    parts = [
        "You are XHX-Agent, a coding assistant that runs in the terminal.",
        "You have access to tools for reading files, searching code, "
        "editing files, and running commands.",
        f"Working directory: {work_dir}",
        f"Platform: {platform.system()} {platform.release()}",
        f"Date: {date.today().isoformat()}",
    ]
    if instructions:
        parts.append(f"\nProject instructions:\n{instructions}")
    return "\n\n".join(parts)
