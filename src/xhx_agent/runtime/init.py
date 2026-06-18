"""项目初始化：创建 .xhx 目录、默认 config/profiles、XHX.md 与仓库索引。

独立于运行时引擎，供 `xhx init` 与测试直接调用，不依赖 RuntimeApp。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from xhx_agent.repo_intel.index import write_repo_intel_index
from xhx_agent.repo_intel.scanner import scan_project
from xhx_agent.repo_intel.xhx_md import write_xhx_md
from xhx_agent.runtime.config import write_default_config
from xhx_agent.runtime.paths import ensure_xhx_dirs
from xhx_agent.runtime.profiles import write_default_profiles


class InitResult(BaseModel):
    config_created: bool
    profiles_created: bool
    xhx_md_created: bool
    repo_index_path: str


def init_project(workspace: Path) -> InitResult:
    """在 ``workspace`` 写入默认配置 + 项目说明 + 仓库索引，返回各项是否新建。"""
    workspace = Path(workspace)
    ensure_xhx_dirs(workspace)
    config_created = write_default_config(workspace)
    profiles_created = write_default_profiles(workspace)
    scan = scan_project(workspace)
    xhx_md_created = write_xhx_md(workspace, scan)
    repo_index = write_repo_intel_index(workspace)
    return InitResult(
        config_created=config_created,
        profiles_created=profiles_created,
        xhx_md_created=xhx_md_created,
        repo_index_path=repo_index.relative_to(workspace).as_posix(),
    )
