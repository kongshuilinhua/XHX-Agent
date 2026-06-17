"""TeamManager：团队生命周期管理。来源：mewcode teams/manager.py。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from xhx_agent.teams.mailbox import Mailbox, MailboxMessage, create_message
from xhx_agent.teams.models import AgentTeam, TeammateInfo, BackendType, unique_team_name, resolve_team_dir
from xhx_agent.teams.progress import TeammateProgress
from xhx_agent.teams.registry import AgentNameRegistry
from xhx_agent.teams.shared_task import SharedTaskStore


class TeamManager:
    """管理 Agent 团队的生命周期。"""

    def __init__(self) -> None:
        self._teams: dict[str, AgentTeam] = {}
        self._task_stores: dict[str, SharedTaskStore] = {}
        self._mailboxes: dict[str, Mailbox] = {}
        self._inprocess_handles: dict[str, Any] = {}
        self._teammate_team_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # team lifecycle
    # ------------------------------------------------------------------

    def create_team(
        self, name: str, lead_agent_id: str,
        description: str = "",
    ) -> AgentTeam:
        team_name = unique_team_name(name)
        team_dir = resolve_team_dir(team_name)
        config_path = team_dir / "config.json"

        team = AgentTeam(
            name=team_name,
            lead_agent_id=lead_agent_id,
            description=description,
            config_path=str(config_path),
        )
        team_dir.mkdir(parents=True, exist_ok=True)
        team.save()

        self._teams[team_name] = team
        self._task_stores[team_name] = SharedTaskStore(team_dir / "tasks.json")
        self._mailboxes[team_name] = Mailbox(team_dir / "mailbox")

        return team

    def get_team(self, name: str) -> AgentTeam | None:
        if name in self._teams:
            return self._teams[name]
        team_dir = resolve_team_dir(name)
        config_path = team_dir / "config.json"
        if config_path.is_file():
            team = AgentTeam.load(str(config_path))
            self._teams[name] = team
            return team
        return None

    def delete_team(self, team_name: str) -> None:
        team = self._teams.get(team_name)
        if team is None:
            return

        # 停止所有活跃成员 + 清理 worktree
        registry = AgentNameRegistry.instance()
        for member in team.members:
            registry.unregister(member.name)
            handle = self._inprocess_handles.pop(member.agent_id, None)
            if handle and hasattr(handle, 'cancel'):
                handle.cancel()
            if member.worktree_path:
                wt = Path(member.worktree_path)
                if wt.exists() and wt != Path(".").resolve():
                    shutil.rmtree(wt, ignore_errors=True)

        # 清理存储
        mb = self._mailboxes.pop(team_name, None)
        if mb:
            mb.cleanup_all()
        self._task_stores.pop(team_name, None)
        self._teams.pop(team_name, None)

        # 删除团队目录（含 trace/evidence 等残留）
        team_dir = resolve_team_dir(team_name)
        if team_dir.exists():
            shutil.rmtree(team_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # member management
    # ------------------------------------------------------------------

    def register_member(self, team_name: str, member: TeammateInfo) -> None:
        team = self.get_team(team_name)
        if team is None:
            return
        team.add_member(member)
        AgentNameRegistry.instance().register(member.name, member.agent_id)
        self._teammate_team_map[member.agent_id] = team_name
        team.save()

    def set_member_idle(self, team_name: str, member_name: str) -> None:
        team = self.get_team(team_name)
        if team is None:
            return
        team.set_member_active(member_name, False)
        # 向 lead agent 通知队友已完成
        mailbox = self._mailboxes.get(team_name)
        if mailbox:
            msg = create_message(
                from_agent="system", to_agent=team.lead_agent_id,
                content=f"Teammate '{member_name}' is now idle and available for new tasks.",
                message_type="text",
            )
            mailbox.write(team.lead_agent_id, msg)

    def drain_lead_mailbox(self, team_name: str) -> list[MailboxMessage]:
        """排空 lead agent 邮箱中的所有待处理消息。

        Lead agent 每轮调用此方法收取队友通知。
        """
        team = self.get_team(team_name)
        if team is None:
            return []
        mailbox = self._mailboxes.get(team_name)
        if mailbox is None:
            return []
        return mailbox.consume(team.lead_agent_id)

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def get_task_store(self, team_name: str) -> SharedTaskStore | None:
        return self._task_stores.get(team_name)

    def get_mailbox(self, team_name: str) -> Mailbox | None:
        return self._mailboxes.get(team_name)

    def get_team_for_teammate(self, agent_id: str) -> str | None:
        return self._teammate_team_map.get(agent_id)

    def register_inprocess_handle(self, agent_id: str, handle: Any) -> None:
        self._inprocess_handles[agent_id] = handle

    def get_all_teammate_progress(self) -> list[TeammateProgress]:
        result: list[TeammateProgress] = []
        for team in self._teams.values():
            for member in team.members:
                if member.progress:
                    result.append(member.progress)
        return result
