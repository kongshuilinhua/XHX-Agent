from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir


class ModelProfile(BaseModel):
    name: str = "default"
    provider: Literal["openai-compatible", "mock"] = "openai-compatible"
    base_url: str = "https://api.example.com/v1"
    api_key_env: str = "XHX_API_KEY"
    model: str = "qwen-plus"
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


def write_default_profiles(workspace: Path) -> bool:
    ensure_xhx_dirs(workspace)
    path = profiles_path(workspace)
    if path.exists():
        return False
    path.write_text(ProfilesFile().model_dump_json(indent=2) + "\n", encoding="utf-8")
    return True


def load_profiles(workspace: Path) -> ProfilesFile:
    path = profiles_path(workspace)
    if not path.exists():
        return ProfilesFile()
    return ProfilesFile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def get_profile(workspace: Path, name: str) -> ModelProfile:
    profiles = load_profiles(workspace).profiles
    for profile in profiles:
        if profile.name == name:
            return profile
    available = ", ".join(profile.name for profile in profiles) or "(none)"
    raise ValueError(f"Unknown profile '{name}'. Available profiles: {available}")
