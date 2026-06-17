from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, global_xhx_dir, xhx_dir


class ModelProfile(BaseModel):
    # 内置占位默认：仅当既无项目级、也无全局 profiles.json 时才生效。
    # 字段值刻意写成显眼的占位（REPLACE_ME / example），避免被误当成可用的真实配置。
    name: str = "default"
    provider: Literal["openai-compatible", "mock"] = "openai-compatible"
    base_url: str = "https://your-provider.example/v1"
    api_key_env: str = "XHX_API_KEY"
    model: str = "REPLACE_ME"
    temperature: float = 0.2
    stream: bool = True
    # 模型上下文窗口（token）。0=未显式配置，由 context_window_for_model 按模型名推断。
    # 压缩阈值与状态栏 Context 用量都以它为基准——对标 Claude 的「窗口跟模型走」。
    context_window: int = 0


# 常见模型上下文窗口（token），按 model 名子串匹配（小写）；命中第一个即返回。
# 不在表内的模型回退 _DEFAULT_CONTEXT_WINDOW。表是保守下限，可被 profile.context_window 覆盖。
_MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("deepseek-v4", 1_000_000),  # deepseek-v4-pro / v4-flash 官方文档：上下文 1M、输出最大 384K
    # 旧别名 deepseek-chat / deepseek-reasoner（2026/07/24 弃用）窗口未在此断言——
    # 如仍用，请在 profile 里用 context_window 显式指定（优先级最高）。
    ("gpt-4o", 128_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4-turbo", 128_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("o4", 200_000),
    ("claude", 200_000),
    ("gemini", 1_000_000),
    ("qwen", 128_000),
    ("glm", 128_000),
    ("moonshot", 128_000),
    ("kimi", 128_000),
    ("llama", 128_000),
    ("mistral", 128_000),
    ("yi-", 200_000),
]
_DEFAULT_CONTEXT_WINDOW = 128_000


def context_window_for_model(model: str) -> int:
    """按模型名子串推断上下文窗口；未知模型回退 _DEFAULT_CONTEXT_WINDOW（保守 128k）。"""
    lowered = (model or "").lower()
    for needle, window in _MODEL_CONTEXT_WINDOWS:
        if needle in lowered:
            return window
    return _DEFAULT_CONTEXT_WINDOW


def resolve_context_window(profile: ModelProfile | None, model: str = "") -> int:
    """解析有效上下文窗口。优先级：profile.context_window 显式值 > 按模型名映射 > 缺省 128k。

    model 显式传入时优先按它匹配（路由场景下实际调用的模型可能与 base profile 不同），
    缺省回退 profile.model。
    """
    explicit = getattr(profile, "context_window", None) if profile is not None else None
    if explicit:
        return int(explicit)
    name = model or (getattr(profile, "model", "") if profile is not None else "")
    return context_window_for_model(name)


def default_profiles() -> list[ModelProfile]:
    return [
        ModelProfile(
            name="mock",
            provider="mock",
            base_url="",
            api_key_env="",
            model="mock",
            temperature=0,
            stream=False,
        ),
        ModelProfile(),
    ]


class ProfilesFile(BaseModel):
    profiles: list[ModelProfile] = Field(default_factory=default_profiles)


def profiles_path(workspace: Path) -> Path:
    return xhx_dir(workspace) / "profiles.json"


def global_profiles_path() -> Path:
    return global_xhx_dir() / "profiles.json"


def write_default_profiles(workspace: Path) -> bool:
    ensure_xhx_dirs(workspace)
    path = profiles_path(workspace)
    if path.exists():
        return False
    path.write_text(ProfilesFile().model_dump_json(indent=2) + "\n", encoding="utf-8")
    return True


def write_global_profiles() -> bool:
    """把默认 profiles 写到 ~/.xhx/profiles.json（已存在则不覆盖）。"""
    path = global_profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    path.write_text(ProfilesFile().model_dump_json(indent=2) + "\n", encoding="utf-8")
    return True


def _read_profiles(path: Path) -> ProfilesFile | None:
    if not path.exists():
        return None
    return ProfilesFile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_profiles(workspace: Path) -> ProfilesFile:
    """项目级 .xhx/profiles.json 优先，其次用户级 ~/.xhx/profiles.json，最后内置默认。"""
    for path in (profiles_path(workspace), global_profiles_path()):
        profiles = _read_profiles(path)
        if profiles is not None:
            return profiles
    return ProfilesFile()


def get_profile(workspace: Path, name: str) -> ModelProfile:
    profiles = load_profiles(workspace).profiles
    for profile in profiles:
        if profile.name == name:
            return profile
    available = ", ".join(profile.name for profile in profiles) or "(none)"
    raise ValueError(f"Unknown profile '{name}'. Available profiles: {available}")
