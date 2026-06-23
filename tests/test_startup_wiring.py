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
