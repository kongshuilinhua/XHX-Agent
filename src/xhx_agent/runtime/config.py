"""项目配置：.xhx/config.json 的结构与读写。

字段即运行时旋钮——max_loop_turns 是 loop 自主循环的硬上限，write_policy 锁死为 apply_patch_only
（只允许结构化补丁写，不放开任意写）。缺文件时回退到内置默认值。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir


class RoutingConfig(BaseModel):
    roles: dict[str, str] = Field(default_factory=dict)
    fallback: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    """.xhx/config.json 的结构；每个字段都是一个运行时旋钮。"""

    version: int = 1
    default_profile: str = "mock"  # 默认离线 mock profile，零配置即可跑
    workspace_root: str = "."
    max_file_bytes: int = 200_000  # 单文件读取上限，防超大文件撑爆上下文预算
    max_loop_turns: int = 20  # loop 自主循环的硬上限，防模型无限迭代
    default_language_targets: list[str] = Field(default_factory=lambda: ["python", "javascript", "typescript"])
    write_policy: Literal["apply_patch_only"] = "apply_patch_only"  # 只允许结构化补丁写，杜绝任意文件写
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    auto_resume: bool = True  # 启动自动接最近会话



def default_config() -> ProjectConfig:
    return ProjectConfig()


def config_path(workspace: Path) -> Path:
    return xhx_dir(workspace) / "config.json"


def write_default_config(workspace: Path) -> bool:
    ensure_xhx_dirs(workspace)
    path = config_path(workspace)
    if path.exists():
        return False
    path.write_text(default_config().model_dump_json(indent=2) + "\n", encoding="utf-8")
    return True


def load_config(workspace: Path) -> ProjectConfig:
    path = config_path(workspace)
    if not path.exists():
        return default_config()
    return ProjectConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
