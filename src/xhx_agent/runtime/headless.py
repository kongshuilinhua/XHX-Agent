"""非交互 headless 驱动：用统一的 Agent 循环把单个任务跑到完成。

供 ``xhx run`` 等命令行入口复用，与交互式 TUI 共享同一套 Agent / 工具 / 权限 / 记忆，
避免出现"交互一套、headless 另一套"的双引擎分裂。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xhx_agent.agents.agent_runner import Agent
from xhx_agent.client import LLMClient
from xhx_agent.config import ProviderConfig
from xhx_agent.hooks import HookEngine, default_verification_hook
from xhx_agent.memory import MemoryManager, load_instructions
from xhx_agent.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from xhx_agent.runtime.profiles import get_profile
from xhx_agent.tools import create_default_registry


@dataclass
class HeadlessResult:
    """headless 一次运行的结构化结果。"""

    status: str  # "completed" | "error"
    summary: str  # 最终的 assistant 文本
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    verification: str = ""  # 启用 verify 时的验证结论（空=未启用/无可验证项）
    turns: int = 0
    changed_files: list[str] | None = None
    # 本次运行的 id：trace 文件（.xhx/traces/<run_id>.jsonl）与会话索引共用同一个，
    # `xhx replay <run_id>` 才能直接回放 `xhx sessions` 列出的运行。
    run_id: str = ""
    # 完整对话历史（record 形式），供会话索引落盘 transcript、`--resume` 全量还原。
    messages: list[dict[str, Any]] | None = None


def _build_permission_checker(work_dir: Path, mode: PermissionMode) -> PermissionChecker:
    """与交互式 TUI 同源的三层规则 + 路径沙箱权限检查器。"""
    home = Path.home()
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(work_dir)),
        rule_engine=RuleEngine(
            user_rules_path=home / ".xhx" / "permissions.json",
            project_rules_path=work_dir / ".xhx" / "permissions.json",
            local_rules_path=work_dir / ".xhx" / "permissions.local.yaml",
        ),
        mode=mode,
    )


def build_headless_agent(
    workspace: str | Path,
    profile: str | None = None,
    *,
    permission_mode: PermissionMode = PermissionMode.DEFAULT,
    max_iterations: int = 50,
    client: LLMClient | None = None,
    verify: bool = False,
) -> Agent:
    """构造一个可在非交互场景下跑到完成的 Agent。

    ``client`` 留作注入口：默认从 profile 解析真实模型客户端，测试时可直接注入。
    ``verify=True`` 时挂上内置 verification 钩子，agent 停止时自动跑变更相关的定向测试。
    """
    ws = Path(workspace).resolve()
    provider: ProviderConfig | None = None
    if client is None:
        profile_name = profile or "default"
        p = get_profile(ws, profile_name)
        if p is None:
            raise RuntimeError(f"Profile '{profile_name}' not found. Run 'xhx init' first.")
        provider = ProviderConfig.from_xhx_profile(p)
        # 主 provider + .xhx/config.json 的 routing.fallback 链；无 fallback 时等价于单个 client。
        from xhx_agent.models.routing import build_agent_client

        client = build_agent_client(ws, provider)

    protocol = provider.protocol if provider is not None else "openai-compat"
    context_window = provider.get_context_window() if provider is not None else 200_000

    registry = create_default_registry(workspace=str(ws))
    checker = _build_permission_checker(ws, permission_mode)

    # 钩子：verify 内置 hook + 用户在 .xhx/config.json 配的 hooks。
    hook_list: list[Any] = []
    if verify:
        hook_list.append(default_verification_hook())
    try:
        from xhx_agent.hooks import load_hooks
        from xhx_agent.runtime.config import load_config

        hook_list.extend(load_hooks(load_config(ws).raw_hooks))
    except Exception:
        pass
    hook_engine = HookEngine(hook_list) if hook_list else None

    agent = Agent(
        client=client,
        registry=registry,
        protocol=protocol,
        work_dir=str(ws),
        max_iterations=max_iterations,
        permission_checker=checker,
        context_window=context_window,
        instructions_content=load_instructions(str(ws)),
        memory_manager=MemoryManager(str(ws)),
        hook_engine=hook_engine,
    )
    # auto 分类器模型：配了 routing.roles["classify"] 的便宜 profile 就用它，没配走主模型。
    if provider is not None:
        from xhx_agent.models.routing import build_role_client

        agent.classifier_client = build_role_client(ws, "classify", provider.name)
    return agent


async def run_headless_task_async(
    workspace: str | Path,
    task: str,
    *,
    profile: str | None = None,
    assume_yes: bool = False,
    max_iterations: int = 50,
    client: LLMClient | None = None,
    verify: bool = False,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    conversation: Any = None,
) -> HeadlessResult:
    """异步把任务跑到完成。``assume_yes`` 时自动放行需确认的工具调用；``verify`` 时停止后自动验证。

    ``conversation`` 传入已还原的 ConversationManager 时在其历史上续跑（`--resume` 全量还原）。
    """
    import time
    import uuid

    mode = PermissionMode.BYPASS if assume_yes else PermissionMode.DEFAULT
    run_id = uuid.uuid4().hex[:12]
    try:
        agent = build_headless_agent(
            workspace,
            profile,
            permission_mode=mode,
            max_iterations=max_iterations,
            client=client,
            verify=verify,
        )
    except Exception as exc:  # 配置/构造期失败：直接返回 error，不抛给 CLI
        return HeadlessResult(status="error", summary="", error=str(exc), run_id=run_id)

    # 持久化证据链：`.xhx/traces/<run_id>.jsonl`。写失败不阻断任务（trace 是旁路观测）。
    trace_store = None
    start_time = time.monotonic()
    try:
        from xhx_agent.evidence.store import EvidenceStore

        trace_store = EvidenceStore(Path(workspace).resolve(), run_id)
        trace_store.write_trace("run_start", {"task": task, "profile": profile or "default"})
        agent.trace_store = trace_store
    except Exception:
        trace_store = None

    def _trace_run_end(payload: dict[str, Any]) -> None:
        if trace_store is None:
            return
        try:
            payload["duration_seconds"] = round(time.monotonic() - start_time, 3)
            trace_store.write_trace("run_end", payload)
        except Exception:
            pass

    # 接入 MCP：连接 .xhx/mcp.json 的 server 并注册工具；任务结束（含异常）后 close。
    # 失败不阻断任务，但必须可见：明细写 trace（mcp_error）并转发事件流，否则
    # "配了 server 但模型没有对应工具"完全无从排查。
    mcp_manager = None
    mcp_failures: dict[str, str] = {}
    try:
        from xhx_agent.runtime.mcp_config import load_mcp_servers

        servers = load_mcp_servers(Path(workspace).resolve())
        if servers:
            from xhx_agent.skills.mcp import MCPManager

            mcp_manager = MCPManager()
            await asyncio.to_thread(mcp_manager.connect_all, servers, None)
            mcp_failures.update(mcp_manager.failed_servers)
            mcp_manager.register_tools_to_registry(agent.registry)
    except Exception as exc:
        mcp_failures["(mcp)"] = f"{type(exc).__name__}: {exc}"
        if mcp_manager is not None:
            mcp_manager.close()  # 注册半途失败时释放已建立的连接，别悬挂到进程结束
            mcp_manager = None
    for server_name, err in mcp_failures.items():
        if trace_store is not None:
            try:
                trace_store.write_trace("mcp_error", {"server": server_name, "error": err})
            except Exception:
                pass
        if event_callback:
            event_callback({"type": "mcp_error", "server": server_name, "error": err})

    try:
        try:
            text = await agent.run_to_completion(task, conversation=conversation, event_callback=event_callback)
        except Exception as exc:
            _trace_run_end({"status": "error", "error": str(exc)})
            return HeadlessResult(
                status="error",
                summary="",
                input_tokens=agent.total_input_tokens,
                output_tokens=agent.total_output_tokens,
                error=str(exc),
                run_id=run_id,
                messages=_serialize_conversation(agent),
            )

        verification = ""
        if agent.hook_engine is not None:
            for note in agent.hook_engine.drain_notifications():
                if note.hook_id == "builtin-verification":
                    verification = note.output

        _trace_run_end(
            {
                "status": "completed",
                "changed_files": list(agent.changed_files),
                "verification": verification,
                "summary": text[:500],
            }
        )
        return HeadlessResult(
            status="completed",
            summary=text,
            input_tokens=agent.total_input_tokens,
            output_tokens=agent.total_output_tokens,
            verification=verification,
            # 真实模型迭代数（每次 LLM 调用算一轮），与 trace/replay 的 turns 同源同义。
            turns=agent.last_iterations,
            changed_files=list(agent.changed_files),
            run_id=run_id,
            messages=_serialize_conversation(agent),
        )
    finally:
        if mcp_manager is not None:
            mcp_manager.close()


def _serialize_conversation(agent: Agent) -> list[dict[str, Any]] | None:
    """把 agent 本次运行的完整对话序列化成 record 列表；失败返回 None（transcript 是旁路）。"""
    conv = getattr(agent, "_current_conversation", None)
    if conv is None:
        return None
    try:
        from xhx_agent.runtime.session import messages_to_records

        return messages_to_records(conv.get_messages())
    except Exception:
        return None


def run_headless_task(workspace: str | Path, task: str, **kwargs: Any) -> HeadlessResult:
    """同步封装：内部跑一个独立事件循环。"""
    return asyncio.run(run_headless_task_async(workspace, task, **kwargs))
