from __future__ import annotations

from pathlib import Path
from typing import Any

from xhx_agent.models import build_chat_client
from xhx_agent.models.types import ModelClientError
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import emit_event
from xhx_agent.runtime.profiles import ModelProfile, get_profile


def resolve_profile_for_role(workspace: Path, role: str, base_profile_name: str) -> ModelProfile:
    """role 在 config.routing.roles 里→用映射的 profile；否则→base_profile_name。"""
    config = load_config(workspace)
    profile_name = config.routing.roles.get(role, base_profile_name)
    return get_profile(workspace, profile_name)


class FallbackChatClient:
    """按序包住多个 client；某个抛 ModelClientError 就试下一个，全失败抛最后一个 error。
    保持 chat(messages, tools) 接口。可选 on_fallback(idx, err) 回调用于发事件/trace。
    """

    def __init__(self, clients: list[Any], on_fallback: Any = None) -> None:
        self.clients = clients
        self.on_fallback = on_fallback

    def set_delta_callback(self, callback: Any) -> None:
        """把流式增量回调转发给所有支持的被包客户端（streaming 与 fallback 正交）。"""
        for client in self.clients:
            if hasattr(client, "set_delta_callback"):
                client.set_delta_callback(callback)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any:
        last_error = None
        for idx, client in enumerate(self.clients):
            try:
                return client.chat(messages, tools)
            except ModelClientError as e:
                last_error = e
                if self.on_fallback:
                    try:
                        self.on_fallback(idx, e)
                    except Exception:
                        pass
        if last_error is not None:
            raise last_error
        raise ModelClientError(code="no_clients", message="No clients available for chat.")


def build_routed_client(
    workspace: Path,
    *,
    role: str,
    base_profile_name: str,
    event_callback: Any = None,
    build_client_func: Any = None,
) -> Any:
    """解析 role 的主 profile + config.routing.fallback 链，build 出客户端。
    无 fallback 时返回单个普通 client（不包 wrapper）；有 fallback 时返回 FallbackChatClient。
    """
    if build_client_func is None:
        build_client_func = build_chat_client

    primary = resolve_profile_for_role(workspace, role, base_profile_name)
    config = load_config(workspace)
    fallback_names = [n for n in config.routing.fallback if n != primary.name]

    clients = [build_client_func(primary)]
    for name in fallback_names:
        profile = get_profile(workspace, name)
        clients.append(build_client_func(profile))

    if len(clients) == 1:
        return clients[0]

    def on_fallback(idx: int, err: Exception) -> None:
        emit_event(
            event_callback,
            "model_fallback",
            f"Model client failed; falling back to index {idx + 1}.",
            index=idx,
            error=str(err),
        )

    return FallbackChatClient(clients, on_fallback=on_fallback)


def build_agent_client(workspace: Path, provider: Any, *, event_callback: Any = None) -> Any:
    """构造 agent 主循环用的 streaming client：主 provider + ``config.routing.fallback`` 链。

    这是被 TUI / headless 真正接入的入口（对比仅给非流式 summarizer 用的 ``build_routed_client``）。
    主 client 构造失败照常抛错（保持既有启动语义）；fallback profile 解析/构造失败则跳过该条，
    不影响主 client 可用。``config.routing.fallback`` 为空（默认）时直接返回单个主 client、不包 wrapper，
    因此对未配置 fallback 的用户零行为变化。
    """
    # 延迟导入，避免 client/config 与本模块的导入期环依赖。
    from xhx_agent.client import FallbackLLMClient, create_client
    from xhx_agent.config import ProviderConfig

    primary = create_client(provider)

    try:
        fallback_names = load_config(workspace).routing.fallback
    except Exception:
        fallback_names = []

    primary_name = getattr(provider, "name", "")
    extra: list[Any] = []
    for name in fallback_names:
        if not name or name == primary_name:
            continue
        try:
            profile = get_profile(workspace, name)
            if profile is None:
                continue
            extra.append(create_client(ProviderConfig.from_xhx_profile(profile)))
        except Exception as err:
            emit_event(
                event_callback,
                "model_fallback_unavailable",
                f"Fallback profile '{name}' unavailable; skipped.",
                profile=name,
                error=str(err),
            )

    if not extra:
        return primary

    def on_fallback(idx: int, err: Exception) -> None:
        emit_event(
            event_callback,
            "model_fallback",
            f"Model stream failed; falling back to next profile (index {idx + 1}).",
            index=idx,
            error=str(err),
        )

    return FallbackLLMClient([primary, *extra], on_fallback=on_fallback)
