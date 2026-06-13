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
