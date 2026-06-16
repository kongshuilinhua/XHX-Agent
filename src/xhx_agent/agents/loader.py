"""Agent 加载器：三层加载（项目 > 用户 > 内置）+ 热重载。

来源：mewcode agents/loader.py，适配 XHX-Agent。
"""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

from xhx_agent.agents.parser import AgentDef, AgentParseError, parse_agent_file, parse_frontmatter, _validate_agent_meta

log = logging.getLogger(__name__)

# XHX-Agent 路径约定
PROJECT_AGENTS_DIR = ".xhx/agents"
USER_AGENTS_DIR = "~/.xhx/agents"
BUILTINS_PACKAGE = "xhx_agent.agents.builtins"


class AgentLoader:
    """三层 Agent 加载器。

    优先级（高→低）：
        1. 项目级  (.xhx/agents/*.md)
        2. 用户级  (~/.xhx/agents/*.md)
        3. 内置    (agents/builtins/*.md)
    """

    def __init__(
        self,
        work_dir: str,
        enable_verification: bool = False,
    ) -> None:
        self._work_dir = work_dir
        self._enable_verification = enable_verification
        self._agents: dict[str, AgentDef] = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, AgentDef]:
        """加载全部 Agent 定义并按优先级去重。"""
        seen: dict[str, AgentDef] = {}

        # 1. 项目级（最高优先级）
        project_path = Path(self._work_dir) / PROJECT_AGENTS_DIR
        for agent_def in self._scan_directory(project_path, "project"):
            if agent_def.agent_type not in seen:
                seen[agent_def.agent_type] = agent_def

        # 2. 用户级
        user_path = Path(USER_AGENTS_DIR).expanduser()
        for agent_def in self._scan_directory(user_path, "user"):
            if agent_def.agent_type not in seen:
                seen[agent_def.agent_type] = agent_def

        # 3. 内置
        for agent_def in self._load_builtins():
            if agent_def.agent_type not in seen:
                seen[agent_def.agent_type] = agent_def

        self._agents = seen
        return seen

    def get(self, agent_type: str) -> AgentDef | None:
        """获取指定类型的 Agent 定义（支持热重载）。"""
        cached = self._agents.get(agent_type)
        if cached is None:
            return None

        # 热重载：文件修改后自动重新解析
        if cached.file_path is not None and cached.file_path.exists():
            try:
                reloaded = parse_agent_file(cached.file_path)
                reloaded.source = cached.source
                self._agents[agent_type] = reloaded
                return reloaded
            except AgentParseError as e:
                log.warning("Hot reload failed for %s, using cached: %s", agent_type, e)

        return cached

    def list_agents(self) -> list[tuple[str, str]]:
        """返回 ``[(agent_type, description), ...]``。"""
        return [(ad.agent_type, ad.when_to_use) for ad in self._agents.values()]

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _scan_directory(self, path: Path, source: str) -> list[AgentDef]:
        """扫描目录下的 .md 文件并解析为 AgentDef 列表。"""
        results: list[AgentDef] = []
        if not path.is_dir():
            return results

        for entry in sorted(path.iterdir()):
            if not entry.is_file() or entry.suffix != ".md":
                continue
            try:
                agent_def = parse_agent_file(entry)
                agent_def.source = source
                agent_def.file_path = entry
                results.append(agent_def)
            except AgentParseError as e:
                log.warning("Skipping agent file %s: %s", entry, e)
        return results

    def _load_builtins(self) -> list[AgentDef]:
        """加载内置 Agent 定义（从包内 builtins/ 目录）。"""
        results: list[AgentDef] = []
        try:
            builtins_pkg = importlib.resources.files(BUILTINS_PACKAGE)
        except (ModuleNotFoundError, TypeError):
            log.warning("Could not load built-in agents package")
            return results

        for item in builtins_pkg.iterdir():
            if not item.name.endswith(".md"):
                continue
            try:
                raw = item.read_text(encoding="utf-8")
                meta, body = parse_frontmatter(raw)
                _validate_agent_meta(meta, item.name)

                agent_def = AgentDef(
                    agent_type=meta["name"],
                    when_to_use=meta["description"],
                    system_prompt=body,
                    tools=meta.get("tools", []),
                    disallowed_tools=meta.get("disallowedTools", []),
                    model=str(meta.get("model", "inherit")),
                    max_turns=meta.get("maxTurns", 50),
                    permission_mode=str(meta.get("permissionMode", "default")),
                    background=bool(meta.get("background", False)),
                    file_path=None,
                    source="builtin",
                )

                if agent_def.agent_type == "Verification" and not self._enable_verification:
                    continue

                results.append(agent_def)
            except (AgentParseError, Exception) as e:
                log.warning("Skipping built-in agent %s: %s", item.name, e)

        return results
