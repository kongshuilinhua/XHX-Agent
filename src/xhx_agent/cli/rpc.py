"""JSON-RPC 2.0 循环：从 stdin 逐行读取请求，驱动新栈 Agent 执行。

不再依赖 RuntimeApp；init / repo-index / run 各自使用独立函数或 headless 驱动。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from xhx_agent.repo_intel.index import diagnose_repo_intel_index, write_repo_intel_index
from xhx_agent.runtime.headless import run_headless_task_async
from xhx_agent.runtime.init import init_project


def _make_rpc_event_callback() -> Any:
    """构造新栈 event_callback（接收 dict），适配 JSON-RPC 通知格式。"""

    def callback(event: dict[str, Any]) -> None:
        notification = {
            "jsonrpc": "2.0",
            "method": "event",
            "params": event,
        }
        sys.stdout.write(json.dumps(notification, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return callback


def send_rpc_response(req_id: Any, result: Any) -> None:
    resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def send_rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    resp = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message, **({"data": data} if data is not None else {})},
    }
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def start_rpc_loop() -> None:
    """逐行读取 JSON-RPC 2.0 消息，分派到 init / repo-index / run / exit。"""
    # 确保 stdout / stdin 行缓冲 + utf-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")  # type: ignore
        except Exception:
            pass
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8")  # type: ignore
        except Exception:
            pass

    workspace = Path.cwd()

    for line in sys.stdin:
        line_str = line.strip()
        if not line_str:
            continue

        try:
            msg = json.loads(line_str)
        except json.JSONDecodeError as e:
            send_rpc_error(None, -32700, f"Parse error: {e}")
            continue

        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            send_rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
            continue

        method = msg.get("method")
        params = msg.get("params", {})
        req_id = msg.get("id")

        if method == "exit":
            send_rpc_response(req_id, "Goodbye")
            break

        if method == "init":
            try:
                res = init_project(workspace)
                send_rpc_response(req_id, res.model_dump())
            except Exception as e:
                send_rpc_error(req_id, -32603, f"Internal error during init: {e}")
            continue

        if method == "repo-index":
            try:
                refresh = params.get("refresh", False)
                if refresh:
                    write_repo_intel_index(workspace)
                diag = diagnose_repo_intel_index(workspace)
                send_rpc_response(req_id, diag.model_dump())
            except Exception as e:
                send_rpc_error(req_id, -32603, f"Internal error during repo-index: {e}")
            continue

        if method == "run":
            task = params.get("task")
            if not task:
                send_rpc_error(req_id, -32602, "Invalid params: 'task' is required.")
                continue

            profile = params.get("profile")
            yes = params.get("yes", False)

            try:
                run_res = asyncio.run(
                    run_headless_task_async(
                        workspace,
                        task,
                        profile=profile,
                        assume_yes=yes,
                        event_callback=_make_rpc_event_callback(),
                    )
                )
                send_rpc_response(req_id, {
                    "status": run_res.status,
                    "summary": run_res.summary,
                    "input_tokens": run_res.input_tokens,
                    "output_tokens": run_res.output_tokens,
                    "turns": run_res.turns,
                    "verification": run_res.verification,
                    "changed_files": run_res.changed_files or [],
                })
            except Exception as e:
                send_rpc_error(req_id, -32603, f"Internal error during run: {e}")
            continue

        send_rpc_error(req_id, -32601, f"Method not found: {method}")
