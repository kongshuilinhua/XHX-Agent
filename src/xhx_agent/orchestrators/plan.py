from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from xhx_agent.models import build_chat_client
from xhx_agent.models.types import ModelClientError
from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, execute_tool_call
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.repo_intel.xhx_md import render_xhx_md
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import emit_event
from xhx_agent.runtime.session import save_transcript

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult

PLAN_SYSTEM_PROMPT = (
    "You are xhx-agent in PLAN mode (Plan-and-Execute). First think through the whole task, then emit a "
    "COMPLETE batch of tool calls to accomplish it in as few model turns as possible (read/search to gather "
    "evidence, then apply_patch for every edit). Use relative paths only; all writes go through apply_patch. "
    "After your edits the system will run verification; if it reports a failure, fix the code and continue. "
    "When the task is fully done, reply with a short natural-language summary and no tool calls."
)


class PlanOrchestrator:
    """plan 范式：Plan-and-Execute（tool-calling）。批量规划→执行→验证路由 + 有界自修复（≤2）。"""

    name = "plan"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.evidence.report import write_report
        from xhx_agent.runtime.app import RunResult

        client = build_chat_client(ctx.profile)
        schemas = ctx.kernel.tool_registry.tool_schemas()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT + "\n\n" + render_xhx_md(ctx.scan)},
        ]
        if ctx.prior_messages:
            messages.extend(m for m in ctx.prior_messages if m.get("role") != "system")
        messages.append({"role": "user", "content": ctx.task})

        changed_files: list[str] = []
        risks: list[str] = []
        max_turns = load_config(ctx.original_workspace).max_loop_turns
        state: dict[str, Any] = {"answer": None}

        status, turns_used = self._drive(
            ctx, client, schemas, messages, changed_files, risks, max_turns, start_turn=1, state=state
        )
        answer = state["answer"]

        verification, verification_results, commands_run, repair_attempts, repair_decision, turns_used = (
            self._verify_and_repair(
                ctx, client, schemas, messages, changed_files, risks, max_turns, status, turns_used, state
            )
        )
        answer = state["answer"]

        summary = write_report(
            workspace=ctx.original_workspace,
            run_id=ctx.run_id,
            task=ctx.task,
            plan=[f"plan paradigm: {turns_used} turn(s)."],
            changed_files=sorted(set(changed_files)),
            commands=commands_run,
            verification=verification,
            risks=risks,
            verification_results=verification_results,
            repair=repair_decision,
            repair_attempts=repair_attempts,
        )
        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        return RunResult(
            run_id=ctx.run_id,
            status=status,
            turns=turns_used,
            changed_files=sorted(set(changed_files)),
            commands=commands_run,
            verification=verification,
            verification_results=verification_results,
            repair=repair_decision,
            repair_attempts=repair_attempts,
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks,
            mode=ctx.mode or "plan",
            answer=answer,
            transcript_path=transcript_rel,
        )

    def _verify_and_repair(
        self,
        ctx: OrchestratorContext,
        client: Any,
        schemas: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        changed_files: list[str],
        risks: list[str],
        max_turns: int,
        status: str,
        turns_used: int,
        state: dict[str, Any],
    ) -> tuple[str, list[Any], list[str], int, Any, int]:
        """plan 招牌：执行产生 changed_files 后跑验证；失败且 auto_repair 时把失败回喂模型继续修（≤2 轮）。"""
        from xhx_agent.runtime.verify_loop import _refresh_repo_intel_index
        from xhx_agent.safety.repair import decide_repair

        verification = "skipped_no_changes"
        verification_results: list[Any] = []
        commands_run: list[str] = []
        repair_attempts = 0
        repair_decision = None
        if not changed_files or status in {"failed", "cancelled"}:
            return verification, verification_results, commands_run, repair_attempts, repair_decision, turns_used

        from xhx_agent.verification.router import infer_verification

        _refresh_repo_intel_index(ctx.workspace, ctx.evidence, ctx.event_callback, risks)
        while True:
            vplan = infer_verification(ctx.workspace, sorted(set(changed_files)))
            if not vplan.commands:
                verification = vplan.skip_reason or "not_executed"
                break
            verification_results = []
            ok = True
            requires_confirmation = False
            for cmd in vplan.commands:
                er = ctx.kernel.run_verification(
                    cmd.command,
                    assume_yes=ctx.assume_yes,
                    confirm_callback=ctx.confirm_callback,
                    event_callback=ctx.event_callback,
                )
                commands_run.append(cmd.command)
                verification_results.append(er)
                if er.status == "confirm":
                    requires_confirmation = True
                    ok = False
                    break
                if er.status != "success":
                    ok = False
            if ok:
                verification = "passed"
            elif requires_confirmation:
                verification = "requires_confirmation"
            elif any(r.status == "failed" for r in verification_results):
                verification = "failed"
            else:
                verification = "not_executed"

            repair_decision = decide_repair(
                verification, attempts_used=repair_attempts, auto_repair_enabled=ctx.auto_repair
            )
            ctx.evidence.write_trace("repair_decision", repair_decision.model_dump())
            if verification != "failed" or not repair_decision.should_repair:
                if verification == "failed":
                    risks.append(f"Verification failed and repair not applied: {repair_decision.reason}")
                break

            repair_attempts += 1
            err = next(
                (
                    (r.stderr or r.stdout or r.summary)
                    for r in verification_results
                    if r.status == "failed" and (r.stderr or r.stdout or r.summary)
                ),
                "tests failed",
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Verification failed:\n{err}\n"
                        "Fix the code so the tests pass. Use apply_patch, then stop."
                    ),
                }
            )
            repair_cap = min(max_turns, turns_used + 2)
            status, turns_used = self._drive(
                ctx, client, schemas, messages, changed_files, risks, repair_cap, start_turn=turns_used + 1, state=state
            )
            _refresh_repo_intel_index(ctx.workspace, ctx.evidence, ctx.event_callback, risks)
            if status in {"failed", "cancelled"}:
                break

        return verification, verification_results, commands_run, repair_attempts, repair_decision, turns_used

    def _drive(
        self,
        ctx: OrchestratorContext,
        client: Any,
        schemas: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        changed_files: list[str],
        risks: list[str],
        max_turns: int,
        *,
        start_turn: int,
        state: dict[str, Any],
    ) -> tuple[str, int]:
        """tool-calling 自主多轮循环：批量规划→执行→（回纯文本即停）。

        在 messages/changed_files/risks 上原地累加；最终自然语言回答写入 state["answer"]。
        返回 (status, turns_used)。供首轮规划与（Task 3）修复轮共用。
        """
        status = "success"
        turns_used = start_turn - 1
        for turn in range(start_turn, max_turns + 1):
            turns_used = turn
            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"
                risks.append("Run cancelled before model call.")
                return status, turns_used
            try:
                result = client.chat(messages, schemas)
            except ModelClientError as exc:
                ctx.evidence.write_trace("model_error", {"turn": turn, **exc.to_trace_payload()})
                emit_event(ctx.event_callback, "model_error", exc.message, turn=turn, code=exc.code)
                status = "failed"
                risks.append(exc.message)
                return status, turns_used

            if not result.tool_calls:
                answer = result.content or ""
                state["answer"] = answer
                messages.append({"role": "assistant", "content": answer})
                emit_event(
                    ctx.event_callback,
                    "model_plan",
                    f"plan answer [turn {turn}]",
                    turn=turn,
                    step_count=0,
                    status="done",
                )
                return status, turns_used

            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                        }
                        for tc in result.tool_calls
                    ],
                }
            )

            def _run(tc, turn=turn):
                return execute_tool_call(ctx, tc, turn)

            reg = ctx.kernel.tool_registry

            def _is_readonly(tc, reg=reg) -> bool:
                d = reg.definition(tc.name)
                return d is not None and d.read_only

            all_readonly = len(result.tool_calls) >= 2 and all(_is_readonly(tc) for tc in result.tool_calls)
            if all_readonly:
                import concurrent.futures

                emit_event(
                    ctx.event_callback,
                    "subagent_concurrent",
                    f"Concurrently exploring {len(result.tool_calls)} read-only steps.",
                    turn=turn,
                    step_count=len(result.tool_calls),
                )
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
                    outcomes = list(pool.map(_run, result.tool_calls))
            else:
                outcomes = [_run(tc) for tc in result.tool_calls]

            for tc, content, changed in outcomes:
                emit_event(ctx.event_callback, "tool_result", "Tool execution completed.", turn=turn, tool=tc.name)
                changed_files.extend(changed)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
        else:
            status = "failed"
            risks.append(f"plan did not finish within {max_turns} turn(s).")
        return status, turns_used
