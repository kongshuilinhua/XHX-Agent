from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from xhx_agent.evidence.report import write_report
from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.planner.planner import DAGPlanner, DAGScheduler
from xhx_agent.planner.reviewer import Reviewer
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.tools.registry import ToolContext
from xhx_agent.tools.terminal import TerminalResult

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult, RuntimeApp

ConfirmationCallback = Callable[[str, object], bool]
CancelCheck = Callable[[], bool]


def _cancel_requested(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:
        return False


def _refresh_repo_intel_index(
    workspace: Path,
    evidence: EvidenceStore,
    event_callback: EventCallback | None,
    risks: list[str],
) -> None:
    from xhx_agent.repo_intel.index import write_repo_intel_index
    try:
        path = write_repo_intel_index(workspace)
    except Exception as exc:  # noqa: BLE001 - repo index refresh should not discard a successful patch
        message = f"Repo intelligence index refresh failed: {exc}"
        risks.append(message)
        evidence.write_trace("repo_index_refresh", {"status": "failed", "error": str(exc)})
        emit_event(event_callback, "repo_index_refresh", "Repo intelligence index refresh failed.", status="failed", error=str(exc))
        return
    relative_path = path.relative_to(workspace).as_posix()
    evidence.write_trace("repo_index_refresh", {"status": "success", "path": relative_path})
    emit_event(event_callback, "repo_index_refresh", "Repo intelligence index refreshed.", status="success", path=relative_path)


class DAGRunner:
    def __init__(self, app: RuntimeApp) -> None:
        self.app = app
        self.workspace = app.workspace

    def run_dag(
        self,
        task: str,
        run_id: str,
        evidence: EvidenceStore,
        kernel: SafeExecutionKernel,
        tool_context: ToolContext,
        assume_yes: bool,
        confirm_callback: ConfirmationCallback | None,
        event_callback: EventCallback | None,
        cancel_check: CancelCheck | None,
        start_time: float,
        metrics_tracker: dict[str, int],
    ) -> RunResult:
        from xhx_agent.evals.metrics import RunMetrics
        from xhx_agent.models.types import ToolStep
        from xhx_agent.runtime.app import RunResult

        planner = DAGPlanner(self.workspace)
        dag_plan = planner.plan_dag(task)

        emit_event(event_callback, "model_plan", f"DAG Plan: {task}", step_count=len(dag_plan.nodes), status="planned")
        evidence.write_trace("model_plan", dag_plan.model_dump())

        import threading
        state_lock = threading.Lock()

        changed_files: list[str] = []
        commands_run: list[str] = []
        verification_results: list[TerminalResult] = []
        risks: list[str] = []

        def execute_node(node):
            if _cancel_requested(cancel_check):
                return False, "Cancelled by user"

            if node.tool == "terminal":
                emit_event(event_callback, "verification_start", f"DAG node verify: {node.description}", command=node.arguments.get("command", ""))
                res = kernel.run_verification(
                    node.arguments.get("command", ""),
                    assume_yes=assume_yes,
                    confirm_callback=confirm_callback,
                    event_callback=event_callback
                )
                emit_event(event_callback, "verification_result", "DAG node verify finished.", command=node.arguments.get("command", ""), status=res.status, exit_code=res.exit_code)
                with state_lock:
                    verification_results.append(res)
                    commands_run.append(node.arguments.get("command", ""))
                return (res.status == "success", res.summary or "Command executed successfully")
            else:
                emit_event(event_callback, "tool_start", f"DAG node tool: {node.description}", tool=node.tool)
                step = ToolStep(tool=node.tool, arguments=node.arguments)
                res, tr, pol = kernel.execute_tool(tool_context, step, 1, event_callback)
                if res is None or tr is None:
                    return (False, pol.reason)
                emit_event(event_callback, "tool_result", "DAG node tool finished.", tool=node.tool, status=res.status, summary=res.summary)
                with state_lock:
                    changed_files.extend(res.changed_files)
                return (res.status == "success", res.summary or "Tool executed successfully")

        # Prior check on initial changes if any (should typically be empty at start)
        if changed_files:
            kernel.create_checkpoint(sorted(set(changed_files)))

        scheduler = DAGScheduler(self.workspace)
        dag_success = scheduler.execute(dag_plan, execute_node)

        if changed_files:
            _refresh_repo_intel_index(self.workspace, evidence, event_callback, risks)

        try:
            from xhx_agent.skills.hooks import hooks_manager
            hooks_manager.trigger("after_verify", workspace=self.workspace, results=verification_results)
        except Exception:
            pass

        reviewer = Reviewer()
        review_dec = reviewer.review(task, changed_files, verification_results)

        status = "success" if (dag_success and review_dec.passed) else "failed"
        if not review_dec.passed:
            risks.append(review_dec.reason)

        verification_status = "passed" if status == "success" else "failed"
        if not changed_files:
            verification_status = "skipped_no_changes"

        try:
            from xhx_agent.skills.hooks import hooks_manager
            hooks_manager.trigger(
                "before_summary",
                workspace=self.workspace,
                run_id=run_id,
                task=task,
                status=status,
                changed_files=changed_files
            )
        except Exception:
            pass

        summary = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task=task,
            plan=[n.description for n in dag_plan.nodes],
            changed_files=sorted(set(changed_files)),
            commands=commands_run,
            verification=verification_status,
            risks=risks,
            verification_results=verification_results,
        )
        evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        emit_event(
            event_callback,
            "run_end",
            "DAG task completed.",
            run_id=run_id,
            status=status,
            verification=verification_status,
            changed_files=sorted(set(changed_files)),
            summary_path=str(summary.relative_to(self.workspace)),
        )
        metrics = RunMetrics(
            duration_seconds=round(time.time() - start_time, 2),
            turns=1,
            tokens_estimate=metrics_tracker["tokens"],
            files_changed_count=len(changed_files),
            commands_run_count=len(commands_run),
            repair_attempts=0,
            success=(status == "success"),
        )
        return RunResult(
            run_id=run_id,
            status=status,
            turns=1,
            changed_files=sorted(set(changed_files)),
            commands=commands_run,
            verification=verification_status,
            verification_results=verification_results,
            summary_path=str(summary.relative_to(self.workspace)),
            risk_summary=risks,
            metrics=metrics,
        )
