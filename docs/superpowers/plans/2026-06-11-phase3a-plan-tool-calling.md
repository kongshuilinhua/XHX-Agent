# Phase 3a：`plan` 范式迁到 tool-calling（批量计划-执行 + 验证/修复） 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development，逐任务执行。步骤用 `- [ ]`。

**Goal:** 把 `plan` 范式（Plan-and-Execute）的模型交互从**手写 ModelPlan-JSON**（`PlannerAgent`→`{summary,status,steps}`→`validate_plan`→`_run_linear`）迁到**原生 tool-calling**，保留 `plan` 的招牌资产——**验证路由 + 有界自修复（≤2）**——与**自主多轮**停止策略。模型一轮产出**一组** `tool_calls`（批量规划），执行后若有改动则跑验证；失败且开 `--auto-repair` 时，把验证失败**回喂进同一个 tool-calling 循环**让模型修。

**本切片边界（3a，纯增量、低风险）：**
- **只重指 `plan` 这一个 mode key** 到新的 tool-calling 编排器（显式 `--mode plan`）。
- **不碰** 默认路由：`linear`（无 `--mode` 时 CLI 默认）/`dag`/`graph` 仍走旧 `_run_linear`/ModelPlan 路径，**现有非 plan 测试零改动**。
- ModelPlan / `PlannerAgent` / `CoderAgent` / `validate_plan` **保留**（linear/dag/graph/repair 仍用）。
- `linear` 与自动分类默认切到新 plan + 收敛停止策略 = **留给 3b**。

**与 `loop` 的区别（portfolio 叙事核心）：** `loop`=逐步 ReAct（每个工具结果回来再决定下一步，无自动验证）；`plan`=前置批量规划→执行→**自动验证路由 + 有界自修复**（工程严谨度范式）。两者协议都用 tool-calling，只差控制流——这正是"一套基座、多范式可对比"的实证。

**Tech Stack:** Python 3.13、pydantic、pytest、uv。
**上位文档：** [ROADMAP](../../../ROADMAP.md) §2（范式表）+ Phase 3。
**运行约定（Windows）：** 命令前置 `PYTHONUTF8=1`；测试 `uv run pytest -q`；lint `uv run ruff check .`。

---

## 已核实的现状（写代码前必读对照）
- `orchestrators/loop.py::LoopOrchestrator`：已是干净的 tool-calling 循环；内含 `_exec_one(tc, turn)`（命令工具→`kernel.run_command_tool`；结构化工具→`kernel.execute_tool`；逐工具 try/except 回喂错误）、模块级 `_render_tool_content`、`_default_verify_command`、`save_transcript` 落盘、最终 answer 入 messages（Phase 2c）。
- `orchestrators/plan.py::PlanOrchestrator`（现状）：`name="plan"`，仅 `ctx.autonomous=True; return ctx.app._run_linear(ctx)`。**本计划整体重写它。**
- `orchestrators/base.py::OrchestratorContext`：有 `task/run_id/workspace/original_workspace/profile/scan/evidence/kernel/tool_context/assume_yes/confirm_callback/auto_repair/cancel_check/event_callback/mode/prior_messages` 等。
- `kernel.execute_tool(tool_context, step, turn, event_callback)` → `(result|None, trace|None, policy)`；`kernel.run_command_tool(command, *, evidence_kind, assume_yes, confirm_callback, event_callback, turn)` → `ToolExecutionResult`。
- `verification.router.infer_verification(workspace, changed_files)` → `VerificationPlan(commands=[VerificationCommand(command, reason, risk)], skip_reason)`。
- `safety.repair.decide_repair(verification_status, attempts_used, auto_repair_enabled)` → `RepairDecision(should_repair, attempts_used, max_attempts, reason)`；`MAX_REPAIR_ATTEMPTS=2`。**注意：`auto_repair_enabled=False` 时永不修**（与现状一致）。
- `verify_loop._refresh_repo_intel_index(workspace, evidence, event_callback, risks)` 可复用刷新索引。
- `RunResult` 字段：`run_id/status/turns/changed_files/commands/verification/verification_results/summary_path/risk_summary/mode/answer/transcript_path/...`。
- mock `chat()`：edit 任务首轮回 `read_file` tool_call，见到 tool 结果后回文本——故 mock 驱动 plan 只读不改、不触发验证（OK，验证路径用 fake client 测）。

---

## Task 1：抽共享工具执行 helper（loop 行为不变）

把 `loop._exec_one` 的"单个 tool_call 执行+安全路由+错误回喂"逻辑抽成共享函数，供 loop 与新 plan 共用，**避免安全路由逻辑两份拷贝**。这是行为保持的重构——loop 测试必须全绿。

**Files:** 新建 `src/xhx_agent/orchestrators/_toolturn.py`；Modify `src/xhx_agent/orchestrators/loop.py`；Test 复用现有 `tests/test_loop_orchestrator.py`（不新增，回归即可）。

- [ ] **Step 1: 新建 `_toolturn.py`**，把 loop 里 `_exec_one` 的函数体与 `_render_tool_content`、`_default_verify_command`、`_MAX_TOOL_RESULT_CHARS` 迁来（loop 改为从此 import，保持引用）：
```python
from __future__ import annotations

import json
from typing import Any

from xhx_agent.models.types import ToolStep
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.runtime.events import emit_event

_MAX_TOOL_RESULT_CHARS = 8000


def execute_tool_call(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str]]:
    """执行单个 tool_call：命令工具走 kernel.run_command_tool，结构化工具走 kernel.execute_tool；
    逐工具 try/except，错误转成可回喂模型的文本。返回 (tc, content, changed_files)。"""
    emit_event(ctx.event_callback, "tool_start", f"Tool execution started: {tc.name}", turn=turn, tool=tc.name)
    d = ctx.kernel.tool_registry.definition(tc.name)
    if d is not None and d.is_command:
        command = str(tc.arguments.get("command") or _default_verify_command(ctx.scan))
        try:
            exec_result = ctx.kernel.run_command_tool(
                command,
                evidence_kind="test" if tc.name == "verify" else "command",
                assume_yes=ctx.assume_yes, confirm_callback=ctx.confirm_callback,
                event_callback=ctx.event_callback, turn=turn)
            return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
        except Exception as exc:  # noqa: BLE001
            ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
            return tc, f"[{tc.name} error] {exc}", []
    step = ToolStep(tool=tc.name, arguments=tc.arguments)
    try:
        exec_result, _trace, policy = ctx.kernel.execute_tool(ctx.tool_context, step, turn, ctx.event_callback)
        if exec_result is None:
            return tc, f"Tool denied/blocked: {policy.reason}", []
        return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
    except Exception as exc:  # noqa: BLE001
        ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
        return tc, f"[{tc.name} error] {exc}", []


def _default_verify_command(scan: Any) -> str:
    langs = getattr(scan, "detected_languages", []) or []
    if "python" in langs:
        return "python -m pytest"
    if "javascript" in langs or "typescript" in langs:
        return "npm test"
    return "python -m pytest"


def _render_tool_content(result: Any) -> str:
    if result.status != "success":
        return f"[{result.tool} failed] {result.error or result.summary}"
    payload = result.trace_payload or {}
    for key in ("content", "results"):
        if key in payload:
            return f"{result.summary}\n{json.dumps(payload[key], ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]}"
    return result.summary
```
> 注意：`execute_tool_call` 的签名与 loop 内 `_exec_one` 略不同（loop 的 `_exec_one(tc, turn=turn)` 是闭包捕获 `ctx`）。这里把 `ctx` 显式传入。loop 改造见 Step 2。

- [ ] **Step 2: 改 `loop.py` 用共享 helper**：
  - 顶部 import：`from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, _render_tool_content, execute_tool_call`。
  - 删除 loop 内的 `_exec_one` 闭包，改为在并发/串行处直接调用共享 helper。把原来的：
    ```python
    if all_readonly:
        ... pool.map(_exec_one, result.tool_calls) ...
    else:
        outcomes = [_exec_one(tc) for tc in result.tool_calls]
    ```
    换成（用 `lambda tc: execute_tool_call(ctx, tc, turn)`，注意闭包变量 `turn` 用默认参数绑定避免 B023）：
    ```python
    def _run(tc, turn=turn):
        return execute_tool_call(ctx, tc, turn)
    if all_readonly:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
            outcomes = list(pool.map(_run, result.tool_calls))
    else:
        outcomes = [_run(tc) for tc in result.tool_calls]
    ```
  - 删除 loop.py 末尾已迁走的模块级 `_default_verify_command` / `_render_tool_content`（现从 `_toolturn` import）；loop 内对 `_MAX_TOOL_RESULT_CHARS` 的引用改用 import 来的。
  - `reg.definition(tc.name).read_only` 那段 all_readonly 判断保持不变。

- [ ] **Step 3: 回归** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q` 全绿（terminal 路由、deny 回喂、并发、transcript、最终 answer 等全过），再 `PYTHONUTF8=1 uv run pytest -q` 零回归。
- [ ] **Step 4: 提交**
```bash
git add src/xhx_agent/orchestrators/_toolturn.py src/xhx_agent/orchestrators/loop.py
git commit -m "refactor(orchestrators): extract shared execute_tool_call helper (loop unchanged)"
```

---

## Task 2：新 `PlanOrchestrator`（批量规划 + tool-calling 执行 + 停止策略）

重写 `plan.py`：tool-calling 自主多轮循环，批量规划系统提示，只读 tool_calls 并发（带 `subagent_concurrent` 事件），自主多轮直到模型停止调工具。**本 Task 先不接验证**（Task 3 接），但 registry 已重指 plan。

**Files:** 重写 `src/xhx_agent/orchestrators/plan.py`；Test 改写 `tests/test_runtime.py` 两处 + 新增 `tests/test_plan_orchestrator.py`。

- [ ] **Step 1: 写/改测试**
  1. **改写 `tests/test_runtime.py`** 的 `test_plan_*`（约 :943 自主多轮、:988 只读并发）：旧版用 `app._build_plan = fake_build_plan`(ModelPlan) 驱动，新版改用 monkeypatch `plan` 模块的 `build_chat_client` 返回 fake `chat` 客户端（参考 `tests/test_loop_orchestrator.py` 的 fake 写法）。
     - 自主多轮：fake 依次回 `apply_patch a.py` → `apply_patch b.py` → 纯文本(done)；断言 `a.py`、`b.py` 都存在、`result.status=="success"`、不是首改即停（执行了≥2 个改动轮）。
     - 只读并发：fake 一轮回两个 `read_file`(a.txt/b.txt) tool_calls → 纯文本；断言两个 read 都执行、有 `subagent_concurrent` 事件。
     > 这两个测试从"验证旧 ModelPlan plan"迁成"验证新 tool-calling plan"，覆盖等价行为。apply_patch 的 patch 文本沿用旧测试里的 `*** Begin Patch ... *** Add File: a.py\n+x = 1\n*** End Patch\n`。
  2. **新增 `tests/test_plan_orchestrator.py`**：
     - `test_plan_conversational_no_changes`: fake 一轮回纯文本 → `result.status=="success"`、`result.answer` 非空、`result.changed_files==[]`、`result.mode=="plan"`、`result.verification` 为 skip 类（无改动不验证）。
     - `test_plan_registry`: `select_orchestrator("plan").name=="plan"`（仍成立）。

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py tests/test_runtime.py -q`

- [ ] **Step 3: 重写 `plan.py`**（参照 loop.py 控制流；用共享 `execute_tool_call`）：
```python
from __future__ import annotations

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
    """plan 范式：Plan-and-Execute（tool-calling）。批量规划→执行→（Task 3）验证+有界修复。"""

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
        answer: str | None = None
        status = "success"
        turns_used = 0

        turns_used = self._drive(ctx, client, schemas, messages, changed_files, risks, max_turns, start_turn=1)
        # _drive 返回 (status, turns_used, answer) —— 见下；这里用元组解构
        # （实现时直接让 _drive 返回三元组并赋值）

        # 占位：Task 3 在此插入「验证 + 有界修复」；本 Task 先 verification="not_executed"
        verification = "not_executed"
        summary = write_report(
            workspace=ctx.original_workspace, run_id=ctx.run_id, task=ctx.task,
            plan=[f"plan paradigm: {turns_used} turn(s)."],
            changed_files=sorted(set(changed_files)), commands=[],
            verification=verification, risks=risks)
        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        return RunResult(
            run_id=ctx.run_id, status=status, turns=turns_used,
            changed_files=sorted(set(changed_files)), commands=[],
            verification=verification,
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks, mode=ctx.mode or "plan", answer=answer,
            transcript_path=transcript_rel)
```
  实现要点（把上面 `_drive` 占位展开成真实循环；也可不抽 `_drive`、直接内联，但要返回 status/turns/answer 三个值）：
  - 每轮 `client.chat(messages, schemas)`；`ModelClientError` → status="failed"、记 trace/event、break。
  - **回纯文本**（无 tool_calls）→ `answer=content`、`messages.append(assistant content)`、emit `model_plan ... status=done`、break。
  - **回 tool_calls** → append assistant(tool_calls)（格式同 loop.py：`{"id","type":"function","function":{"name","arguments":json.dumps(...)}}`）；判 all_readonly（≥2 且全 `definition().read_only`）→ 并发并 emit `subagent_concurrent`，否则串行；用共享 `execute_tool_call(ctx, tc, turn)`；逐结果 emit `tool_result`、累加 changed_files、append role:tool（截断 `_MAX_TOOL_RESULT_CHARS`）。
  - 多轮上限 `max_turns`；耗尽 → status="failed"、risk 记 "plan did not finish within N turns"。
  - cancel_check：每轮开头查 `ctx.cancel_check`，命中 → status="cancelled"、break（与 loop 一致）。
  - 取消子 agent 并发事件名沿用 loop 风格；`subagent_concurrent` 事件 payload 至少含 `turn, step_count`。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py tests/test_runtime.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（全绿；注意 registry 已重指，但 plan.py 仍是合法编排器）。
- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/plan.py tests/test_plan_orchestrator.py tests/test_runtime.py
git commit -m "feat(plan): tool-calling Plan-and-Execute orchestrator (batch planning, stop policy)"
```

---

## Task 3：验证路由 + 有界自修复（回喂 tool-calling）

给 `PlanOrchestrator` 接上招牌：执行产生 changed_files 后跑验证；失败且 `ctx.auto_repair` 时把失败回喂模型继续修（≤2 轮，`decide_repair` 门控）。

**Files:** Modify `src/xhx_agent/orchestrators/plan.py`；Test 扩 `tests/test_plan_orchestrator.py`。

- [ ] **Step 1: 写失败测试**（用 python fixture：tmp 项目含一个会失败的测试，fake apply_patch 修好它）
  - `test_plan_runs_verification_after_changes`: 建 tmp python 项目（`src/calc.py` 含 `def add(a,b): return a-b  # bug`，`tests/test_calc.py` 断言 `add(1,2)==3`）；`RuntimeApp(tmp).init_project()`；monkeypatch `plan.build_chat_client` 让 fake 回一个把 `a-b` 改成 `a+b` 的 `apply_patch`，再回纯文本 done；`run_task(..., mode="plan", assume_yes=True)`（assume_yes 让 CONFIRM 档 pytest 真跑）；断言 `result.changed_files` 含 calc.py、`result.verification` 为 `passed`（或 verification_results 里 pytest success）。
  - `test_plan_repair_fed_back_on_failure`（可选但推荐）：fake 第一轮 apply_patch 改成仍错的值（如 `a*b`），验证失败；`auto_repair=True`；fake 第二轮（看到回喂的 "Verification failed" user 消息后）apply_patch 改成 `a+b`；断言最终 verification `passed`、repair_attempts≥1。
     > 若构造确定性 fixture 太繁，至少保留第一个验证测试 + 一个"`auto_repair=False` 时失败不修、verification=failed、不崩"的测试。
  - 参考既有 `tests/test_runtime.py` 里 python fixture 的构造方式（calc.py/TODO_BUG 模式），patch 文本要能干净 apply。

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py -q`

- [ ] **Step 3: 在 `plan.py` 执行循环之后插入验证+修复**（替换 Task 2 的 `verification="not_executed"` 占位）：
```python
        from xhx_agent.runtime.verify_loop import _refresh_repo_intel_index
        from xhx_agent.safety.repair import decide_repair
        from xhx_agent.verification.router import infer_verification

        verification = "skipped_no_changes"
        verification_results = []
        commands_run: list[str] = []
        repair_attempts = 0
        if changed_files and status not in {"failed", "cancelled"}:
            _refresh_repo_intel_index(ctx.workspace, ctx.evidence, ctx.event_callback, risks)
            while True:
                vplan = infer_verification(ctx.workspace, sorted(set(changed_files)))
                if not vplan.commands:
                    verification = vplan.skip_reason or "not_executed"
                    break
                verification_results = []
                ok = True
                for cmd in vplan.commands:
                    er = ctx.kernel.run_command_tool(
                        cmd.command, evidence_kind="test", assume_yes=ctx.assume_yes,
                        confirm_callback=ctx.confirm_callback, event_callback=ctx.event_callback,
                        turn=turns_used)
                    commands_run.append(cmd.command)
                    verification_results.append(er)
                    if er.status != "success":
                        ok = False
                verification = "passed" if ok else ("failed" if any(r.status == "failed" for r in verification_results) else "not_executed")
                decision = decide_repair(verification, attempts_used=repair_attempts, auto_repair_enabled=ctx.auto_repair)
                ctx.evidence.write_trace("repair_decision", decision.model_dump())
                if verification != "failed" or not decision.should_repair:
                    if verification == "failed":
                        risks.append(f"Verification failed and repair not applied: {decision.reason}")
                    break
                repair_attempts += 1
                err = next((r.error for r in verification_results if r.status == "failed" and r.error), "tests failed")
                messages.append({"role": "user",
                    "content": f"Verification failed:\n{err}\nFix the code so the tests pass. Use apply_patch, then stop."})
                turns_used = self._drive(ctx, client, schemas, messages, changed_files, risks,
                                         max_turns, start_turn=turns_used + 1)  # 继续 tool-calling 修
                _refresh_repo_intel_index(ctx.workspace, ctx.evidence, ctx.event_callback, risks)
```
  - 把 `verification` / `verification_results` / `commands_run` / `repair_attempts` 灌进 `write_report`（commands=commands_run）与 `RunResult`（`verification=verification, verification_results=verification_results, commands=commands_run, repair_attempts=repair_attempts`）。
  - **注意验证用 `ctx.workspace`（worktree 活动区，改动在此），report/transcript 用 `ctx.original_workspace`**——与 loop/`_run_linear` 一致。
  - `run_command_tool` 对 CONFIRM 档（pytest）在 `assume_yes=False` 且无 confirm_callback 时返回 status `"confirm"`（未执行）→ 那么 `ok` 为假但也非 "failed"，verification 记 `not_executed`/`confirm`；测试里用 `assume_yes=True` 让其真跑。
  - 为支持"修复继续循环"，把 Task 2 的执行主体抽成 `self._drive(ctx, client, schemas, messages, changed_files, risks, max_turns, start_turn) -> turns_used`（返回新的 turns_used；内部 append 到同一 messages/changed_files/risks）。修复轮 `start_turn=turns_used+1`，且修复阶段建议把单次 `_drive` 的轮上限收紧（如 `min(max_turns, start_turn+2)`）避免修复无限跑——实现者按需控制总轮数不超过合理上限。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（全绿）。
- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/plan.py tests/test_plan_orchestrator.py
git commit -m "feat(plan): verification routing + bounded self-repair fed back into tool-calling loop"
```

---

## Task 4：收尾 —— ruff + 全量 + ROADMAP

- [ ] **Step 1: ruff** — `PYTHONUTF8=1 uv run ruff check .`（`--fix` 清 I001/E501/B023 等；保持全绿）。
- [ ] **Step 2: 全量** — `PYTHONUTF8=1 uv run pytest -q`（全绿，零回归；旧 loop/linear/dag/graph 行为不变）。
- [ ] **Step 3: 真实联调（手动，需 DeepSeek key；由协调者执行，不在 subagent 内）** —— `--mode plan` 跑一个小改造任务，确认模型批量规划→apply_patch→（带 `--yes`）验证。
- [ ] **Step 4: ROADMAP** 在 §4 加一行 `Phase 3a ✅ 已完成（2026-06-11）`：`plan` 范式迁 tool-calling（批量规划+执行+验证路由+有界修复回喂），仅重指 `--mode plan`；`linear`/默认收敛留 3b。并把 §3 改造里"现有 `loop`/`linear` → `plan` 范式"标注"plan 部分已落地（3a）"。
- [ ] **Step 5: 提交**
```bash
git add ROADMAP.md docs/superpowers/plans/2026-06-11-phase3a-plan-tool-calling.md
git commit -m "docs(roadmap): mark Phase 3a (plan -> tool-calling) done"
```

---

## 自检（对照 3a 范围）
- ✅ `--mode plan` 用原生 tool-calling（退役该范式的 ModelPlan）→ Task 2-3
- ✅ 批量规划 + 自主多轮 + 只读并发 → Task 2
- ✅ 验证路由 + 有界自修复（回喂 tool-calling、`auto_repair` 门控、≤2 轮）→ Task 3
- ✅ 共享 `execute_tool_call`、loop 行为不变 → Task 1
- ✅ 默认 linear/dag/graph 与现有非 plan 测试零改动；ModelPlan 保留 → 全程边界
- ⏭ linear/默认切换 + 停止策略收敛 → 3b（不在本切片）
