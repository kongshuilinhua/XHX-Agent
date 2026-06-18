"""SkillExecutor — 技能执行器。"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class SkillExecutor:
    """管理技能的生命周期：加载、注入 prompt、Fork 模式。"""

    def __init__(
        self,
        loader: Any = None,
        agent: Any = None,
        client: Any = None,
        protocol: str = "openai-compat",
        **kwargs: Any,
    ) -> None:
        self._loader = loader
        self._agent = agent
        self._client = client
        self._protocol = protocol
        self.active_skills: dict[str, str] = {}

    def get_active_skill_names(self) -> list[str]:
        return list(self.active_skills.keys())

    async def execute_inline(self, skill_name: str, task: str) -> str:
        """内联执行：将 SOP 注入 agent。"""
        return ""
