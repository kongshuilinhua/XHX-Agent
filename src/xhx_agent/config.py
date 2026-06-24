"""LLM 提供者配置模型。适配 XHX-Agent 的 runtime/config 到新 Agent 系统。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderConfig:
    """LLM 提供者配置：protocol、模型名、API key、endpoint 等。"""

    name: str = ""
    protocol: str = "openai-compat"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    context_window: int = 200_000
    thinking: bool = False
    max_output_tokens: int = 4096

    # 来自 XHX-Agent 的旧式配置
    _legacy: dict[str, Any] = field(default_factory=dict)

    # 运行时从模型端点拉取到的上下文窗口缓存（0=未拉取）。见 client.fetch_and_cache_context_window。
    _fetched_context_window: int = 0

    def get_max_output_tokens(self) -> int:
        return self.max_output_tokens

    def get_context_window(self) -> int:
        return self.context_window or self._fetched_context_window

    def set_fetched_context_window(self, window: int) -> None:
        """缓存从端点拉取到的上下文窗口，供后续调用复用，避免重复网络请求。"""
        self._fetched_context_window = window

    def resolve_api_key(self) -> str:
        """解析 API key：优先使用配置值，其次环境变量。"""
        import os

        if self.api_key:
            return self.api_key
        # 按协议尝试常见环境变量
        env_vars = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openai-compat": "OPENAI_API_KEY",
        }
        env_var = env_vars.get(self.protocol, "OPENAI_API_KEY")
        return os.environ.get(env_var, "")

    @classmethod
    def from_xhx_profile(cls, profile: Any) -> ProviderConfig:
        """从 XHX-Agent 的 ModelProfile 创建 ProviderConfig。"""
        import os

        # 解析 API key：ModelProfile 存的是环境变量名
        api_key = ""
        api_key_env = getattr(profile, "api_key_env", "XHX_API_KEY")
        if api_key_env:
            api_key = os.environ.get(api_key_env, "")

        # mock provider 走确定性本地客户端，不发网络请求（用于测试 / 离线）。
        provider_kind = getattr(profile, "provider", "") or ""
        protocol = "mock" if provider_kind == "mock" or profile.model == "mock" else "openai-compat"

        return cls(
            name=profile.name,
            protocol=protocol,
            base_url=profile.base_url or "",
            model=profile.model or "",
            api_key=api_key,
            context_window=profile.context_window or 200_000,
            max_output_tokens=getattr(profile, "max_output_tokens", 4096) or 4096,
        )


@dataclass
class MCPServerConfig:
    """MCP server 配置。"""

    name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    auth_token: str = ""
    auth_token_env: str = ""


@dataclass
class WorktreeConfig:
    """Worktree 配置。"""

    enabled: bool = True
    auto_cleanup: bool = True
    symlink_directories: list[str] = field(default_factory=list)
    stale_cleanup_interval: int = 300
    stale_cutoff_hours: int = 24


@dataclass
class AppConfig:
    """应用级配置（简化版，用于新 Agent 系统）。"""

    permission_mode: str = "default"
    worktree: dict[str, Any] = field(default_factory=dict)
    providers: list[ProviderConfig] = field(default_factory=list)
