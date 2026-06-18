from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult

MAX_TIMEOUT = 600


class Params(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=120, description="Timeout in seconds (max 600)")


class Bash(Tool):
    name = "Bash"
    description = "Execute a shell command and return stdout and stderr."
    params_model = Params
    category = "command"

    # 用户中断时不杀进程
    interrupt_behavior: str = "cancel"

    async def execute(self, params: Params) -> ToolResult:
        from xhx_agent.agent import cancel_reason

        timeout = min(params.timeout, MAX_TIMEOUT)
        proc = None
        cancelled = False
        try:
            proc = await asyncio.create_subprocess_shell(
                params.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return ToolResult(
                output=f"Error: command timed out after {timeout}s", is_error=True
            )
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
                                line = await asyncio.wait_for(
                                    proc.stdout.readline(), timeout=0.3
                                )
                                if not line:
                                    break
                                stdout_lines.append(line.decode(errors="replace"))
                        except (asyncio.TimeoutError, Exception):
                            pass
                    if proc and proc.stderr:
                        try:
                            while True:
                                line = await asyncio.wait_for(
                                    proc.stderr.readline(), timeout=0.3
                                )
                                if not line:
                                    break
                                stderr_lines.append(line.decode(errors="replace"))
                        except (asyncio.TimeoutError, Exception):
                            pass
                    prefix = "(process running in background - use ps/tasklist to check)\n\n"
                    stdout = prefix.encode() if not stdout_lines else (
                        prefix.encode()
                        + "".join(stdout_lines).encode()
                    )
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
        return ToolResult(output=output, is_error=proc.returncode != 0)
