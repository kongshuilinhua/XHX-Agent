"""非交互 headless 驱动：用统一的 Agent 循环把单个任务跑到完成。

供 ``xhx run`` 等命令行入口复用，与交互式 TUI 共享同一套 Agent / 工具 / 权限 / 记忆，
避免出现"交互一套、headless 另一套"的双引擎分裂。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xhx_agent.agents.agent_runner import Agent
from xhx_agent.client import LLMClient, create_client
from xhx_agent.config import ProviderConfig
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


def _build_permission_checker(work_dir: Path, mode: PermissionMode) -> PermissionChecker:
    """与交互式 TUI 同源的三层规则 + 路径沙箱权限检查器。"""
    home = Path.home()
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=RuleEngine(
            user_rules_path=home / ".XHX" / "permissions.json",
            project_rules_path=work_dir / ".XHX" / "permissions.json",
            local_rules_path=work_dir / ".XHX" / "permissions.local.yaml",
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
) -> Agent:
    """构造一个可在非交互场景下跑到完成的 Agent。

    ``client`` 留作注入口：默认从 profile 解析真实模型客户端，测试时可直接注入。
    """
    ws = Path(workspace).resolve()
    provider: ProviderConfig | None = None
    if client is None:
        profile_name = profile or "default"
        p = get_profile(ws, profile_name)
        if p is None:
            raise RuntimeError(f"Profile '{profile_name}' not found. Run 'xhx init' first.")
        provider = ProviderConfig.from_xhx_profile(p)
        client = create_client(provider)

    protocol = provider.protocol if provider is not None else "openai-compat"
    context_window = provider.get_context_window() if provider is not None else 200_000

    registry = create_default_registry(workspace=str(ws))
    checker = _build_permission_checker(ws, permission_mode)

    return Agent(
        client=client,
        registry=registry,
        protocol=protocol,
        work_dir=str(ws),
        max_iterations=max_iterations,
        permission_checker=checker,
        context_window=context_window,
        instructions_content=load_instructions(str(ws)),
        memory_manager=MemoryManager(str(ws)),
    )


async def run_headless_task_async(
    workspace: str | Path,
    task: str,
    *,
    profile: str | None = None,
    assume_yes: bool = False,
    max_iterations: int = 50,
    client: LLMClient | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> HeadlessResult:
    """异步把任务跑到完成。``assume_yes`` 时自动放行需确认的工具调用。"""
    mode = PermissionMode.DONT_ASK if assume_yes else PermissionMode.DEFAULT
    try:
        agent = build_headless_agent(
            workspace, profile, permission_mode=mode, max_iterations=max_iterations, client=client
        )
    except Exception as exc:  # 配置/构造期失败：直接返回 error，不抛给 CLI
        return HeadlessResult(status="error", summary="", error=str(exc))

    try:
        text = await agent.run_to_completion(task, event_callback=event_callback)
    except Exception as exc:
        return HeadlessResult(
            status="error",
            summary="",
            input_tokens=agent.total_input_tokens,
            output_tokens=agent.total_output_tokens,
            error=str(exc),
        )

    return HeadlessResult(
        status="completed",
        summary=text,
        input_tokens=agent.total_input_tokens,
        output_tokens=agent.total_output_tokens,
    )


def run_headless_task(workspace: str | Path, task: str, **kwargs: Any) -> HeadlessResult:
    """同步封装：内部跑一个独立事件循环。"""
    return asyncio.run(run_headless_task_async(workspace, task, **kwargs))
