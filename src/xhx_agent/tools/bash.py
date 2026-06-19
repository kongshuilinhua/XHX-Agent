from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult

MAX_TIMEOUT = 600

# 常见"永不自行退出"的 dev server / watcher：即使模型忘了 run_in_background 也兜底转后台，
# 避免前台同步等待把会话卡死到超时。
_SERVER_PATTERNS = (
    "http.server",
    "manage.py runserver",
    "flask run",
    "uvicorn ",
    "gunicorn ",
    "npm start",
    "npm run dev",
    "npm run serve",
    "yarn dev",
    "pnpm dev",
    "vite",
    "webpack serve",
    "webpack-dev-server",
    "php -s",
    "rails server",
    "rails s ",
)


def _looks_like_server(command: str) -> bool:
    low = command.lower()
    return any(p in low for p in _SERVER_PATTERNS)


class Params(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=120, description="Timeout in seconds (max 600)")
    run_in_background: bool = Field(
        default=False,
        description=(
            "Run detached and return immediately instead of waiting. Use for long-running "
            "commands that never exit on their own — dev servers (python -m http.server, "
            "npm start, flask/uvicorn run), file watchers, etc. Otherwise the call blocks "
            "until timeout. A trailing ' &' is also treated as background."
        ),
    )


class Bash(Tool):
    name = "Bash"
    description = (
        "Execute a shell command and return stdout and stderr. For long-running commands like "
        "dev servers or watchers, set run_in_background=true so it launches detached and returns "
        "immediately (otherwise the call blocks until timeout)."
    )
    params_model = Params
    category = "command"

    # 用户中断时不杀进程
    interrupt_behavior: str = "cancel"

    def _run_detached(self, command: str) -> ToolResult:
        """后台启动：进程脱离父进程组、立即返回，不阻塞会话（用于 dev server 等）。"""
        import subprocess
        import sys

        kwargs: dict = {
            "shell": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(command, **kwargs)
        except Exception as e:
            return ToolResult(output=f"Error launching background command: {e}", is_error=True)

        return ToolResult(
            output=(
                f"Launched in background (PID {proc.pid}). It keeps running independently — "
                f"the call returned immediately without waiting. Command: {command}"
            )
        )

    async def execute(self, params: Params) -> ToolResult:  # type: ignore[override]
        from xhx_agent.agent import cancel_reason

        command = params.command.strip()
        background = params.run_in_background or command.endswith("&") or _looks_like_server(command)
        if background:
            return self._run_detached(command.rstrip("&").strip())

        timeout = min(params.timeout, MAX_TIMEOUT)
        proc = None
        cancelled = False
        try:
            proc = await asyncio.create_subprocess_shell(
                params.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            return ToolResult(output=f"Error: command timed out after {timeout}s", is_error=True)
        except asyncio.CancelledError:
            cancelled = True
            reason = cancel_reason.get("")
            if reason == "interrupt":
                try:
                    stdout_lines: list[str] = []
                    stderr_lines: list[str] = []
                    if proc and proc.stdout:
                        try:
                            while True:
                                line = await asyncio.wait_for(proc.stdout.readline(), timeout=0.3)
                                if not line:
                                    break
                                stdout_lines.append(line.decode(errors="replace"))
                        except (TimeoutError, Exception):
                            pass
                    if proc and proc.stderr:
                        try:
                            while True:
                                line = await asyncio.wait_for(proc.stderr.readline(), timeout=0.3)
                                if not line:
                                    break
                                stderr_lines.append(line.decode(errors="replace"))
                        except (TimeoutError, Exception):
                            pass
                    prefix = "(process running in background - use ps/tasklist to check)\n\n"
                    stdout = prefix.encode() if not stdout_lines else (prefix.encode() + "".join(stdout_lines).encode())
                    stderr = "".join(stderr_lines).encode() if stderr_lines else b""
                except Exception:
                    stdout = b"(process running in background)\n"
                    stderr = b""
            else:
                raise
        except Exception as e:
            return ToolResult(output=f"Error executing command: {e}", is_error=True)
        finally:
            if proc is not None and proc.returncode is None and not cancelled:
                proc.kill()
                await proc.wait()

        parts: list[str] = []
        if stdout:
            parts.append(f"STDOUT:\n{stdout.decode(errors='replace')}")
        if stderr:
            parts.append(f"STDERR:\n{stderr.decode(errors='replace')}")
        if not parts:
            parts.append("(no output)")

        output = "\n".join(parts)
        # 能走到这里 proc 必已创建（创建失败会在上面的 except 提前返回）
        assert proc is not None
        return ToolResult(output=output, is_error=proc.returncode != 0)
