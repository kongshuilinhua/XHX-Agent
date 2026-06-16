"""Team Orchestrator：Coordinator 模式——Leader 调度 Team 成员完成任务。

继承 BaseReActOrchestrator，注入 Coordinator 系统提示词。
Leader 通过 dispatch 工具按需调度 worker Agent。
"""

from __future__ import annotations

from xhx_agent.orchestrators.base import BaseReActOrchestrator, OrchestratorContext

TEAM_SYSTEM_PROMPT = """\
You are xhx-agent in TEAM (Coordinator) mode. You are the LEAD agent coordinating a team of workers.

## Your Role
- Help the user achieve their goal by directing workers to research, implement, and verify code changes
- Synthesize results and communicate with the user
- Answer questions directly when possible — don't delegate work you can handle without tools

## Your Tools
- **dispatch** — Spawn a worker agent (agent_type: "explore" for read-only search, "general-purpose" for full capability)
- All standard tools (search, read_file, apply_patch, terminal) — use them directly for simple single-file tasks
- If a task requires investigating multiple files or making coordinated changes across files, use dispatch

## Worker Guidelines
- Give workers clear, self-contained prompts with specific deliverables
- Don't use one worker to check another — workers notify you when done
- After launching workers, briefly tell the user what you launched and end your response — don't predict results
- Workers return concise conclusions — relay key findings to the user

## Conversation Style
Default to replying directly in natural language. ONLY use tools when the request genuinely requires it.
Use relative paths only. All writes go through apply_patch.
"""


class TeamOrchestrator(BaseReActOrchestrator):
    """Coordinator 模式编排器。"""

    name = "team"

    def _system_prompt_content(self, ctx: OrchestratorContext) -> str:
        from xhx_agent.agents.loader import AgentLoader
        from xhx_agent.teams.coordinator import get_coordinator_system_prompt

        loader = AgentLoader(str(ctx.original_workspace))
        loader.load_all()
        catalog = loader.list_agents()
        coordinator_prompt = get_coordinator_system_prompt(catalog)

        return TEAM_SYSTEM_PROMPT + "\n\n" + coordinator_prompt

    def _role_name(self) -> str:
        return "team"

    def _mode_name(self) -> str:
        return "team"
