from __future__ import annotations

import json
import sys
from typing import Any

from xhx_agent.repo_intel.index import diagnose_repo_intel_index, write_repo_intel_index
from xhx_agent.runtime.app import RuntimeApp


def make_rpc_event_callback() -> Any:
    def callback(event_type: str, message: str, **kwargs: Any) -> None:
        notification = {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "event": event_type,
                "message": message,
                "details": kwargs
            }
        }
        sys.stdout.write(json.dumps(notification, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return callback


def send_rpc_response(req_id: Any, result: Any) -> None:
    resp = {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result
    }
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def send_rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    resp = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": code,
            "message": message,
            **({"data": data} if data is not None else {})
        }
    }
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def start_rpc_loop() -> None:
    """Read lines of JSON-RPC 2.0 messages from stdin and execute commands."""
    # Ensure stdout and stdin are line-buffered and use utf-8
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True, encoding="utf-8") # type: ignore
        except Exception:
            pass
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8") # type: ignore
        except Exception:
            pass

    app = RuntimeApp()

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
                res = app.init_project()
                send_rpc_response(req_id, res.model_dump())
            except Exception as e:
                send_rpc_error(req_id, -32603, f"Internal error during init: {e}")
            continue

        if method == "repo-index":
            try:
                refresh = params.get("refresh", False)
                if refresh:
                    write_repo_intel_index(app.workspace)
                diag = diagnose_repo_intel_index(app.workspace)
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
            auto_repair = params.get("auto_repair", False)

            try:
                # Execute run_task and stream events
                run_res = app.run_task(
                    task=task,
                    profile_name=profile,
                    assume_yes=yes,
                    auto_repair=auto_repair,
                    event_callback=make_rpc_event_callback()
                )
                send_rpc_response(req_id, run_res.model_dump())
            except Exception as e:
                send_rpc_error(req_id, -32603, f"Internal error during run: {e}")
            continue

        send_rpc_error(req_id, -32601, f"Method not found: {method}")
