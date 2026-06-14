"""项目配置：.xhx/config.json 的结构与读写。

字段即运行时旋钮——max_loop_turns 是 loop 自主循环的硬上限，write_policy 锁死为 apply_patch_only
（只允许结构化补丁写，不放开任意写）。缺文件时回退到内置默认值。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, global_xhx_dir, xhx_dir


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
    max_subagent_turns: int = 12  # 子 agent（dispatch / graph 节点）循环上限；停止靠"无工具调用"，这是防跑飞的宽松安全网（对标 max_loop_turns，4 太紧会让 explore 撞限）
    default_language_targets: list[str] = Field(default_factory=lambda: ["python", "javascript", "typescript"])
    write_policy: Literal["apply_patch_only"] = "apply_patch_only"  # 只允许结构化补丁写，杜绝任意文件写
    max_parallel_subagents: int = 4  # graph DAG 并发执行的子 agent 数上限，防嵌套 LLM 调用烧 token/撞限流
    max_graph_replans: int = 2  # graph joiner 判定不合格时回 planner 重规划的最多轮数（0=禁用 replan，单轮收尾）；防来回烧 token
    routing: RoutingConfig = Field(default_factory=RoutingConfig)



def default_config() -> ProjectConfig:
    return ProjectConfig()


def config_path(workspace: Path) -> Path:
    return xhx_dir(workspace) / "config.json"


def global_config_path() -> Path:
    return global_xhx_dir() / "config.json"


def write_default_config(workspace: Path) -> bool:
    ensure_xhx_dirs(workspace)
    path = config_path(workspace)
    if path.exists():
        return False
    path.write_text(default_config().model_dump_json(indent=2) + "\n", encoding="utf-8")
    return True


def write_global_config() -> bool:
    """把全局默认 config 写到 ~/.xhx/config.json（已存在则不覆盖）。

    全局默认 default_profile 设为 "default"——用户级配置意味着“我要真模型”，
    而非项目级 init 那种零配置离线 mock。
    """
    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    config = default_config().model_copy(update={"default_profile": "default"})
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return True


def _read_config(path: Path) -> ProjectConfig | None:
    if not path.exists():
        return None
    return ProjectConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_config(workspace: Path) -> ProjectConfig:
    """项目级 .xhx/config.json 优先，其次用户级 ~/.xhx/config.json，最后内置默认。

    模型配置本就是“配一次、到处用”，所以在任意目录启动都能回落到全局配置。
    """
    for path in (config_path(workspace), global_config_path()):
        config = _read_config(path)
        if config is not None:
            return config
    return default_config()
