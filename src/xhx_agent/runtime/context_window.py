"""Context Window 自动探测：从 /v1/models 端点拉取上下文窗口。

来源：mewcode config.py 四层回退链思路，适配 XHX-Agent。

四层回退：
    1. profile.context_window 显式值（最高优先级，已在 profiles.py 实现）
    2. /v1/models API 自动探测（本模块）
    3. 内置模型名子串映射表（profiles.py context_window_for_model）
    4. 保守默认值 128k（profiles.py _DEFAULT_CONTEXT_WINDOW）
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 缓存文件
_CACHE_FILE = ".xhx/context_window_cache.json"
_CACHE_TTL = 86400  # 24 小时


def _load_cache(workspace: Path) -> dict[str, int]:
    """从缓存文件加载已探测的 context window。"""
    cache_path = workspace / _CACHE_FILE
    if not cache_path.is_file():
        return {}

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        ts = data.get("_timestamp", 0)
        if time.time() - ts > _CACHE_TTL:
            return {}  # 过期
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(workspace: Path, entries: dict[str, int]) -> None:
    """保存已探测的 context window 到缓存文件。"""
    cache_path = workspace / _CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"_timestamp": time.time(), **entries}
    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def probe_context_window(
    base_url: str,
    model: str,
    *,
    workspace: Path | None = None,
) -> int | None:
    """通过 /v1/models 端点自动探测模型上下文窗口。

    Args:
        base_url: API 基础 URL。
        model: 模型 ID。
        workspace: 工作目录（用于缓存）。

    Returns:
        探测到的 context_window，失败返回 None。
    """
    # 先查缓存
    if workspace is not None:
        cache = _load_cache(workspace)
        cached = cache.get(model)
        if cached is not None:
            return cached

    try:
        import urllib.request

        url = f"{base_url.rstrip('/')}/v1/models"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        models = data.get("data", data.get("models", []))
        for entry in models:
            entry_model = entry.get("id", entry.get("model", ""))
            if entry_model == model:
                # 不同 API 的上下文窗口字段不同
                window = (
                    entry.get("context_window")
                    or entry.get("max_input_tokens")
                    or entry.get("max_tokens")
                    or entry.get("context_length")
                )
                if window and isinstance(window, (int, float)):
                    w = int(window)
                    if workspace is not None:
                        _save_cache(workspace, {model: w})
                    log.info("Auto-detected context window for %s: %d", model, w)
                    return w

    except Exception as e:
        log.debug("Context window probe failed for %s: %s", model, e)

    return None


def resolve_with_auto(
    profile: Any,
    model: str = "",
    *,
    workspace: Path | None = None,
) -> int:
    """带自动探测的上下文窗口解析。

    完整回退链：
        1. profile.context_window 显式值
        2. /v1/models API 自动探测（如果提供了 base_url + workspace）
        3. 内置模型名子串映射表
        4. 默认 128k
    """
    from xhx_agent.runtime.profiles import context_window_for_model, resolve_context_window

    # Layer 1: 显式值
    explicit = getattr(profile, "context_window", 0) if profile is not None else 0
    if explicit:
        return int(explicit)

    name = model or (getattr(profile, "model", "") if profile is not None else "")

    # Layer 2: API 自动探测
    if workspace is not None and profile is not None:
        base_url = getattr(profile, "base_url", "")
        if base_url and name:
            probed = probe_context_window(base_url, name, workspace=workspace)
            if probed:
                return probed

    # Layer 3 + 4: 内置映射表 → 默认值
    return context_window_for_model(name)
