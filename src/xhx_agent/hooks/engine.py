"""Hook 引擎：事件匹配 + 批量执行 + 通知收集。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from xhx_agent.hooks.executors import execute_action
from xhx_agent.hooks.models import Hook, HookContext

log = logging.getLogger(__name__)


@dataclass
class HookNotification:
    """Hook 执行后的通知记录。"""
    hook_id: str
    event: str
    output: str
    success: bool


class HookEngine:
    """Hook 引擎：管理注册的 Hook、按事件匹配并执行。"""

    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks: list[Hook] = hooks or []
        self._prompt_messages: list[str] = []
        self._notifications: list[HookNotification] = []

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def find_matching_hooks(self, event: str, ctx: HookContext) -> list[Hook]:
        """返回匹配 *event* 且满足条件的 Hook 列表。"""
        matched: list[Hook] = []
        for hook in self.hooks:
            if hook.event != event:
                continue
            if not hook.should_run():
                continue
            if hook.condition is not None and not hook.condition.evaluate(ctx):
                continue
            matched.append(hook)
        return matched

    async def run_hooks(self, event: str, ctx: HookContext) -> None:
        """异步执行匹配的 Hook。

        对 ``reject=True`` 的 pre_tool_use hook：执行后若失败则抛 ToolRejectedError。
        async_exec 的 hook 用 ``asyncio.ensure_future`` 后台执行。
        """
        matched = self.find_matching_hooks(event, ctx)
        for hook in matched:
            hook.mark_executed()
            if hook.async_exec:
                asyncio.ensure_future(self._run_single(hook, ctx))
            else:
                await self._run_single(hook, ctx)

    def run_hooks_sync(self, event: str, ctx: HookContext) -> None:
        """同步执行匹配的 Hook（供 XHX-Agent 线程路径使用）。

        内部用 ``asyncio.run`` 创建临时事件循环。"""
        try:
            loop = asyncio.get_running_loop()
            # 已有运行中的事件循环 → 在新线程跑
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.run_hooks(event, ctx))
                future.result(timeout=60)
        except RuntimeError:
            asyncio.run(self.run_hooks(event, ctx))

    def collect_prompt_messages(self) -> list[str]:
        """收集所有 prompt 型 hook 的输出消息，并清空内部缓冲区。"""
        msgs = self._prompt_messages[:]
        self._prompt_messages.clear()
        return msgs

    def drain_notifications(self) -> list[HookNotification]:
        """消费所有 hook 通知，并清空内部缓冲区。"""
        notifs = self._notifications[:]
        self._notifications.clear()
        return notifs

    def clear(self) -> None:
        """清空所有 Hook 和通知缓冲区。"""
        self.hooks.clear()
        self._prompt_messages.clear()
        self._notifications.clear()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _run_single(self, hook: Hook, ctx: HookContext) -> None:
        """执行单个 Hook 并处理结果。"""
        try:
            result = await execute_action(hook.action, ctx)
            if hook.action.type == "prompt" and result.success:
                self._prompt_messages.append(result.output)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event=hook.event,
                    output=result.output,
                    success=result.success,
                )
            )
            if not result.success:
                log.warning("Hook '%s' action failed: %s", hook.id, result.output)
        except Exception as e:
            log.warning("Hook '%s' execution error: %s", hook.id, e)
            self._notifications.append(
                HookNotification(
                    hook_id=hook.id,
                    event=hook.event,
                    output=str(e),
                    success=False,
                )
            )
