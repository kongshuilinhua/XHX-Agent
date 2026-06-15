"""测试夹具：把全局 ~/.xhx 配置目录隔离到临时目录。

load_config / load_profiles 现在会在项目级配置缺失时回落到用户级 ~/.xhx。
为避免测试读到开发机/CI 上真实的 ~/.xhx（从而行为随机），这里用 autouse fixture
把 XHX_HOME 指向一个每个测试独立的空临时目录——除非测试自己显式 setenv 覆盖。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_global_xhx(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("xhx-home")
    monkeypatch.setenv("XHX_HOME", str(home))
