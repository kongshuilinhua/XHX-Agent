"""runtime/context_window.py 单测：缓存读写、resolve_with_auto 回退链。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from xhx_agent.runtime.context_window import (
    _CACHE_FILE,
    _load_cache,
    _save_cache,
    probe_context_window,
    resolve_with_auto,
)


class _Profile:
    def __init__(self, context_window: int = 0, model: str = "", base_url: str = "") -> None:
        self.context_window = context_window
        self.model = model
        self.base_url = base_url


def test_cache_roundtrip(tmp_path: Path) -> None:
    _save_cache(tmp_path, {"deepseek-x": 128000})
    assert _load_cache(tmp_path) == {"deepseek-x": 128000}


def test_cache_expired(tmp_path: Path) -> None:
    cache_path = tmp_path / _CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"_timestamp": time.time() - 999999, "m": 1000}), encoding="utf-8")
    assert _load_cache(tmp_path) == {}  # 过期被丢弃


def test_cache_missing_dir(tmp_path: Path) -> None:
    assert _load_cache(tmp_path) == {}  # 无缓存文件


def test_resolve_explicit_wins(tmp_path: Path) -> None:
    assert resolve_with_auto(_Profile(context_window=200000), workspace=tmp_path) == 200000


def test_resolve_falls_back_to_model_map() -> None:
    # 无显式值、无 workspace（不探测）→ 走内置映射/默认
    val = resolve_with_auto(_Profile(model="gpt-4o"), model="gpt-4o")
    assert val > 0


def test_resolve_none_profile() -> None:
    assert resolve_with_auto(None, model="some-model") > 0


def test_probe_uses_cache_without_network(tmp_path: Path) -> None:
    _save_cache(tmp_path, {"cached-model": 64000})
    # 命中缓存，不会发起网络请求
    assert probe_context_window("http://unused", "cached-model", workspace=tmp_path) == 64000
