import json
from pathlib import Path

import pytest

from xhx_agent.runtime.config import (
    default_config,
    load_config,
    write_default_config,
    write_global_config,
)
from xhx_agent.runtime.profiles import (
    get_profile,
    load_profiles,
    write_default_profiles,
    write_global_profiles,
)


def test_default_config_and_profile(tmp_path: Path) -> None:
    assert write_default_config(tmp_path)
    assert write_default_profiles(tmp_path)
    config = load_config(tmp_path)
    profile = get_profile(tmp_path, config.default_profile)
    assert config.write_policy == "apply_patch_only"
    assert config.default_profile == "mock"
    assert profile.provider == "mock"
    assert get_profile(tmp_path, "default").provider == "openai-compatible"


def test_default_config_has_max_loop_turns() -> None:
    assert default_config().max_loop_turns == 20


def _set_global_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("XHX_HOME", str(home))


def test_config_falls_back_to_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 全局家目录里有 config，但项目工作区里没有 → load_config 应回落到全局。
    home = tmp_path / "home"
    workspace = tmp_path / "anywhere"
    workspace.mkdir()
    _set_global_home(monkeypatch, home)
    assert write_global_config()  # 写到 ~/.xhx（被重定向到 home）
    config = load_config(workspace)
    assert config.default_profile == "default"  # 全局默认走真模型，而非 mock


def test_project_config_overrides_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "proj"
    workspace.mkdir()
    _set_global_home(monkeypatch, home)
    write_global_config()  # 全局 default_profile=default
    write_default_config(workspace)  # 项目 default_profile=mock
    assert load_config(workspace).default_profile == "mock"  # 项目级优先


def test_profiles_fall_back_to_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "anywhere"
    workspace.mkdir()
    _set_global_home(monkeypatch, home)
    # 在全局写一个真实 provider 的 default profile。
    # XHX_HOME 本身就是全局 .xhx 目录，不再套一层。
    home.mkdir(parents=True)
    (home / "profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "name": "default",
                        "provider": "openai-compatible",
                        "base_url": "https://api.deepseek.com/v1",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "model": "deepseek-chat",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profile = get_profile(workspace, "default")
    assert profile.model == "deepseek-chat"
    assert profile.base_url == "https://api.deepseek.com/v1"


def test_no_config_anywhere_uses_builtin_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # 全局家目录也空 → 回落到内置默认（占位，刻意不可连）。
    _set_global_home(monkeypatch, tmp_path / "empty-home")
    workspace = tmp_path / "anywhere"
    workspace.mkdir()
    assert load_config(workspace).default_profile == "mock"
    profile = get_profile(workspace, "default")
    assert profile.model == "REPLACE_ME"  # 显眼占位，不会被误当真实配置


def test_write_global_helpers_are_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_global_home(monkeypatch, tmp_path / "home")
    assert write_global_config() is True
    assert write_global_profiles() is True
    assert write_global_config() is False  # 已存在不覆盖
    assert write_global_profiles() is False
    assert load_profiles(tmp_path / "fresh").profiles  # 任意目录能读到全局 profiles
