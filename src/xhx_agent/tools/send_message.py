"""SendMessage 工具 — 团队内部消息（真实 mailbox 投递）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from xhx_agent.teams.manager import TeamManager


class SendMessageParams(BaseModel):
    to: str = ""
    content: str = ""


class SendMessageTool(Tool):
    name = "SendMessage"
    description = (
        "Send a message to another teammate or the team lead. "
        "Use to='lead' to message the team lead, or pass a teammate name."
    )
    params_model = SendMessageParams
    category = "command"

    def __init__(
        self,
        team_manager: TeamManager,
        team_name: str,
        agent_name: str,
    ) -> None:
        self._team_manager = team_manager
        self._team_name = team_name
        self._agent_name = agent_name

    async def execute(self, params: BaseModel) -> ToolResult:
        p: SendMessageParams = params  # type: ignore[assignment]

        from xhx_agent.teams.mailbox import create_message

        mailbox = self._team_manager.get_mailbox(self._team_name)
        if mailbox is None:
            return ToolResult(
                output=f"Mailbox not found for team '{self._team_name}'",
                is_error=True,
            )

        # 解析收件人 agent_id
        team = self._team_manager.get_team(self._team_name)
        if team is None:
            return ToolResult(
                output=f"Team '{self._team_name}' not found",
                is_error=True,
            )

        recipient_id: str | None = None
        to = (p.to or "").strip().lower()

        if to == "lead":
            recipient_id = team.lead_agent_id
        elif to:
            for member in team.members:
                if member.name.lower() == to or member.agent_id == to:
                    recipient_id = member.agent_id
                    break

        if recipient_id is None:
            return ToolResult(
                output=(
                    f"Recipient '{p.to}' not found in team '{self._team_name}'. "
                    f"Available: lead" + ("".join(f", {m.name}" for m in team.members) if team.members else "")
                ),
                is_error=True,
            )

        msg = create_message(
            from_agent=self._agent_name,
            to_agent=recipient_id,
            content=p.content,
            summary=p.content[:100],
        )
        mailbox.write(recipient_id, msg)

        return ToolResult(output=f"Message sent to {p.to}.")
