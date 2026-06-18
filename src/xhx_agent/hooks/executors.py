"""Hook 动作执行器：command / prompt / http / agent。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from urllib.error import URLError
from urllib.request import Request, urlopen

from xhx_agent.hooks.models import Action, ActionResult, HookContext

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异步执行器（原始接口）
# ---------------------------------------------------------------------------


async def execute_command(action: Action, ctx: HookContext) -> ActionResult:
    """执行 shell 命令。"""
    command = ctx.expand(action.command)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=action.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ActionResult(
                output=f"Command timed out after {action.timeout}s: {command}",
                success=False,
            )
        output = stdout.decode(errors="replace").strip() if stdout else ""
        return ActionResult(output=output, success=proc.returncode == 0)
    except Exception as e:
        return ActionResult(output=f"Command execution error: {e}", success=False)


async def execute_prompt(action: Action, ctx: HookContext) -> ActionResult:
    """注入提示词（返回展开后的消息文本）。"""
    message = ctx.expand(action.message)
    return ActionResult(output=message, success=True)


async def execute_http(action: Action, ctx: HookContext) -> ActionResult:
    """发送 HTTP 请求。"""
    url = ctx.expand(action.url)
    body = ctx.expand(action.body) if action.body else None
    method = action.method or "POST"

    headers = dict(action.headers)
    for k, v in headers.items():
        headers[k] = ctx.expand(v)
    if body and "Content-Type" not in headers:
        headers["Content-Type"] = "application/json"

    def _do_request() -> ActionResult:
        try:
            data = body.encode() if body else None
            req = Request(url, data=data, headers=headers, method=method)
            with urlopen(req, timeout=30) as resp:
                resp_body = resp.read().decode(errors="replace")[:500]
                return ActionResult(
                    output=f"HTTP {resp.status}: {resp_body}",
                    success=200 <= resp.status < 300,
                )
        except URLError as e:
            return ActionResult(output=f"HTTP error: {e}", success=False)
        except Exception as e:
            return ActionResult(output=f"HTTP error: {e}", success=False)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_request)


async def execute_agent(action: Action, ctx: HookContext) -> ActionResult:
    """触发子 Agent（当前为 stub）。"""
    prompt = ctx.expand(action.prompt)
    log.info("Agent executor stub called with prompt: %s", prompt[:100])
    return ActionResult(output="agent executor not yet implemented", success=True)


_EXECUTOR_MAP = {
    "command": execute_command,
    "prompt": execute_prompt,
    "http": execute_http,
    "agent": execute_agent,
}


async def execute_action(action: Action, ctx: HookContext) -> ActionResult:
    """根据 action.type 分发执行。"""
    executor = _EXECUTOR_MAP.get(action.type)
    if executor is None:
        return ActionResult(
            output=f"Unknown action type: {action.type}",
            success=False,
        )
    return await executor(action, ctx)


# ---------------------------------------------------------------------------
# 同步包装器（供 XHX-Agent 同步代码调用）
# ---------------------------------------------------------------------------


def execute_action_sync(action: Action, ctx: HookContext) -> ActionResult:
    """同步执行 hook 动作。内部用 asyncio 临时事件循环。

    适用于 XHX-Agent 的同步调用路径（如 kernel.execute_tool 内触发
    pre_tool_use / post_tool_use hook）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中的事件循环 → 创建新的
        return asyncio.run(execute_action(action, ctx))

    # 有运行中的事件循环 → 在新线程中跑（避免嵌套事件循环问题）
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, execute_action(action, ctx))
        return future.result(timeout=action.timeout + 10)


# ---------------------------------------------------------------------------
# 同步 exec 执行器（不依赖 asyncio）
# ---------------------------------------------------------------------------


def execute_command_sync(action: Action, ctx: HookContext) -> ActionResult:
    """纯同步执行 shell 命令（适用于 Windows / 非 asyncio 环境）。"""
    command = ctx.expand(action.command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=action.timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return ActionResult(output=output, success=result.returncode == 0)
    except subprocess.TimeoutExpired:
        return ActionResult(
            output=f"Command timed out after {action.timeout}s: {command}",
            success=False,
        )
    except Exception as e:
        return ActionResult(output=f"Command execution error: {e}", success=False)
