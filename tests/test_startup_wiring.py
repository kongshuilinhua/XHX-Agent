"""启动接线回归。

run_textual_console 此前只把 providers 传给 XHXApp，hooks/coordinator/fork/verify
等开关全用默认值——用户在 .xhx/config.json 里配了也不生效。本测试直接调
run_textual_console（monkeypatch 掉 XHXApp 捕获构造参数），验证这些配置真的被读取
并传进 app。
"""

from __future__ import annotations

import json


def test_run_textual_console_wires_config_switches(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.config import config_path, load_config
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    # 在 .xhx/config.json 里打开这些开关 + 配一个 hook
    cfg_path = config_path(tmp_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["enable_coordinator_mode"] = True
    data["enable_fork"] = True
    data["enable_verification_agent"] = True
    data["default_permission_mode"] = "auto"
    data["worktree_symlink_directories"] = ["node_modules", ".venv"]
    data["raw_hooks"] = [{"event": "pre_tool_use", "action": {"type": "command", "command": "echo hi"}}]
    cfg_path.write_text(json.dumps(data), encoding="utf-8")

    captured: dict = {}

    import xhx_agent.tui.app as app_mod

    class FakeApp:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            pass

    monkeypatch.setattr(app_mod, "XHXApp", FakeApp)

    default_profile = load_config(tmp_path).default_profile
    app_mod.run_textual_console(workspace=str(tmp_path), profile=default_profile)

    # 接线通：config 的开关与 hook 都真传给了 app
    assert captured["enable_coordinator_mode"] is True
    assert captured["enable_fork"] is True
    assert captured["enable_verification_agent"] is True
    assert captured["hook_engine"] is not None  # raw_hooks 非空 → 构造了 HookEngine
    assert captured["mcp_servers"] == []  # 没写 mcp.json → 空列表

    from xhx_agent.permissions import PermissionMode

    assert captured["permission_mode"] == PermissionMode.ACCEPT_EDITS  # "auto" → ACCEPT_EDITS
    assert captured["worktree_config"] is not None
    assert captured["worktree_config"].symlink_directories == ["node_modules", ".venv"]


def test_run_textual_console_defaults_off(tmp_path, monkeypatch) -> None:
    """默认 config（开关全 false、无 hook）下，app 收到的开关为 False、hook_engine 为 None。"""
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.config import load_config
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    captured: dict = {}

    import xhx_agent.tui.app as app_mod

    class FakeApp:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            pass

    monkeypatch.setattr(app_mod, "XHXApp", FakeApp)

    default_profile = load_config(tmp_path).default_profile
    app_mod.run_textual_console(workspace=str(tmp_path), profile=default_profile)

    assert captured["enable_coordinator_mode"] is False
    assert captured["enable_fork"] is False
    assert captured["enable_verification_agent"] is False
    assert captured["hook_engine"] is None


def test_headless_agent_loads_user_hooks(tmp_path, monkeypatch) -> None:
    """build_headless_agent 把 .xhx/config.json 的 raw_hooks 接进 agent.hook_engine（即便没开 verify）。"""
    from unittest.mock import Mock

    from xhx_agent.runtime.config import config_path
    from xhx_agent.runtime.init import init_project

    monkeypatch.chdir(tmp_path)
    init_project(tmp_path)
    cfg_path = config_path(tmp_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["raw_hooks"] = [{"event": "pre_tool_use", "action": {"type": "command", "command": "echo hi"}}]
    cfg_path.write_text(json.dumps(data), encoding="utf-8")

    from xhx_agent.runtime.headless import build_headless_agent

    agent = build_headless_agent(tmp_path, client=Mock(), verify=False)
    assert agent.hook_engine is not None  # 用户 hook 真接上了


def test_headless_connects_and_closes_mcp(tmp_path, monkeypatch) -> None:
    """run_headless_task_async 连接 .xhx/mcp.json 的 server 并在任务结束后 close。"""
    import asyncio
    from unittest.mock import Mock

    from xhx_agent.runtime.init import init_project

    monkeypatch.chdir(tmp_path)
    init_project(tmp_path)
    (tmp_path / ".xhx" / "mcp.json").write_text(
        json.dumps({"servers": [{"name": "fs", "command": "node", "args": ["x.js"]}]}),
        encoding="utf-8",
    )

    captured: dict = {"servers": None, "closed": False}

    class FakeMcp:
        def __init__(self, request_timeout: float = 30.0) -> None:
            pass

        def connect_all(self, servers, on_error=None) -> None:
            captured["servers"] = servers

        def register_tools_to_registry(self, registry) -> None:
            pass

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr("xhx_agent.skills.mcp.MCPManager", FakeMcp)

    class FakeAgent:
        registry = object()
        hook_engine = None
        total_input_tokens = 0
        total_output_tokens = 0
        turn_count = 1
        changed_files: list = []

        async def run_to_completion(self, task, event_callback=None) -> str:
            return "done"

    import xhx_agent.runtime.headless as hl

    monkeypatch.setattr(hl, "build_headless_agent", lambda *a, **k: FakeAgent())

    result = asyncio.run(hl.run_headless_task_async(str(tmp_path), "task", client=Mock()))

    assert captured["servers"] is not None and len(captured["servers"]) == 1
    assert captured["closed"] is True  # 任务后 close 了 MCP
    assert result.status == "completed"
