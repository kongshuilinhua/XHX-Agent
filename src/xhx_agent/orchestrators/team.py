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
    _agent_cache: dict[str, tuple[float, list[tuple[str, str]]]] = {}

    def _system_prompt_content(self, ctx: OrchestratorContext) -> str:
        from xhx_agent.agents.loader import AgentLoader
        from xhx_agent.teams.coordinator import get_coordinator_system_prompt

        workspace_key = str(ctx.original_workspace)
        now = __import__("time").time()
        cached = self._agent_cache.get(workspace_key)
        mtime = float((ctx.original_workspace / ".xhx" / "agents").stat().st_mtime) \
            if (ctx.original_workspace / ".xhx" / "agents").is_dir() else 0.0

        if cached and cached[0] >= mtime:
            catalog = cached[1]
        else:
            loader = AgentLoader(workspace_key)
            loader.load_all()
            catalog = loader.list_agents()
            self._agent_cache[workspace_key] = (now, catalog)

        coordinator_prompt = get_coordinator_system_prompt(catalog)
        return TEAM_SYSTEM_PROMPT + "\n\n" + coordinator_prompt

    def _role_name(self) -> str:
        return "team"

    def _mode_name(self) -> str:
        return "team"

    def _before_run(self, ctx: OrchestratorContext, messages: list[dict]) -> None:
        """Team 模式：创建 TeamManager + 注册团队，供 dispatch 子 agent 协调用。"""
        from xhx_agent.teams.manager import TeamManager
        from xhx_agent.teams.models import TeammateInfo, BackendType
        from xhx_agent.teams.registry import AgentNameRegistry

        mgr = TeamManager()
        team = mgr.create_team(
            name=f"team-{ctx.run_id[:8]}",
            lead_agent_id="leader",
            description=f"Team for run {ctx.run_id}",
        )
        # 将 TeamManager 挂到 context 上供工具层使用
        ctx.team_manager = mgr  # type: ignore[attr-defined]
        ctx.team_name = team.name  # type: ignore[attr-defined]

        # 注册 leader
        from xhx_agent.teams.progress import TeammateProgress
        leader_progress = TeammateProgress(name="lead", team_name=team.name, status="running")
        leader = TeammateInfo(
            name="lead", agent_id="leader", agent_type="general-purpose",
            model=getattr(ctx.profile, "model", ""), worktree_path=str(ctx.workspace),
            backend_type=BackendType.IN_PROCESS.value, is_active=True,
            progress=leader_progress,
        )
        mgr.register_member(team.name, leader)

    def _verify_changes(
        self, ctx: OrchestratorContext, changed_files: list[str],
    ) -> tuple[str | None, list[str]]:
        """Team 模式：变更后自动推断并运行验证命令。"""
        if not changed_files:
            return ("skipped_no_changes", [])

        try:
            from xhx_agent.verification.router import infer_verification
            vplan = infer_verification(ctx.original_workspace, changed_files)
            if not vplan or not vplan.commands:
                return ("skipped_no_changes", [])

            commands = [item.command for item in vplan.commands]
            results = []
            for cmd in commands:
                try:
                    r = ctx.kernel.run_verification(
                        cmd, assume_yes=ctx.assume_yes,
                        event_callback=ctx.event_callback,
                    )
                    results.append(r)
                except Exception:
                    pass

            if not results:
                return ("not_executed", commands)

            passed = all(r.status == "success" for r in results)
            return ("passed" if passed else "failed", commands)
        except Exception:
            return ("not_executed", [])
