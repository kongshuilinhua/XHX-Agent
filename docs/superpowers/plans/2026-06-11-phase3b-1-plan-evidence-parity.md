# Phase 3b-1：给 tool-calling `plan` 补齐证据 parity（checkpoint / evidence / patch-binding）

> **执行者须知（FOR THE IMPLEMENTING MODEL — 冷启动、无对话上下文）：** 这是一份**自包含**实现计划。你**没有**本项目的历史对话上下文，所以**严禁凭记忆/猜测**——动任何文件前必须先用读取工具读它的**真实当前内容**，以仓库现状为准。计划里给的代码是蓝图，若与现状的导入路径/字段名/行号有出入，以现状为准做最小适配，但**行为目标不得变**。严格按 Task 顺序、按 `- [ ]` 步骤执行（写失败测试→确认红→最小实现→确认绿→全量回归→提交）。

---

## 0. 背景与目标（必读）

项目 `xhx_agent` 是一个本地编码 agent。它有多种"编排器范式"，其中：
- `loop`（`src/xhx_agent/orchestrators/loop.py`）：ReAct 对话循环。
- `plan`（`src/xhx_agent/orchestrators/plan.py`）：Plan-and-Execute，原生 tool-calling，已有"批量规划→执行→验证路由+有界自修复"。

**问题：** 当前 tool-calling 的 `plan` 编排器是**精简版**——它执行工具、跑验证、修复，但**不产生**旧执行路径（legacy `RuntimeApp._run_linear`，走手写 ModelPlan）那套**富证据**：
1. **工具结果的 evidence 条目**（`evidence.write_evidence(...)`）——尤其 `apply_patch` 的 `kind="patch"` 证据。
2. **patch-evidence-binding** trace——把"工具调用 trace ↔ 证据条目 ↔ 改动文件"绑定起来。
3. **checkpoint / restore plan**——验证前拍 checkpoint，整体失败时生成只读 restore plan，并在 `RunResult` 上暴露 `checkpoint_path` / `restore_plan_path`。

**本切片（3b-1）目标：** 给 tool-calling 的 `plan` 编排器补齐上述 1/2/3 三项证据 parity，使其与 legacy 路径功能对等。**纯增量**：
- **不改默认路由**（`linear` / 自动分类默认仍走 legacy `_run_linear`）。
- **不迁移任何现有测试**、**不改 `mock.py`**。
- **不碰 `loop.py` 的外部行为**（见 Task 1 的做法：只动 `_toolturn.py` 内部，`execute_tool_call` 的对外契约不变）。
- **不做** context-debug 报告 parity（它绑定 `compile_context_pack`，tool-calling 不走那条路；留待后续 3b-2 处理，本切片显式不做）。

**为什么这样切：** 后续 3b-2 才会"把默认切到 tool-calling plan + 迁移约 13 个深度耦合 legacy 证据的测试"。先在 3b-1 把证据 parity 补好，3b-2 的测试迁移才简单。本切片到此为止。

---

## 1. 运行约定（Windows / 本仓）
- 所有命令前置 `PYTHONUTF8=1`（控制台编码）。
- 测试：`PYTHONUTF8=1 uv run pytest -q`（可带具体文件路径，如 `... uv run pytest tests/test_plan_orchestrator.py -q`）。
- Lint：`PYTHONUTF8=1 uv run ruff check .`（必须 `All checks passed!`）。
- **绿色基线 = `313 passed, 1 skipped` + ruff 全绿。** 每个 Task 提交前必须跑全量、零回归、ruff 全绿。
- 只在当前 git 分支/worktree 内工作。**不要** `git push`、不切分支、不 `git reset --hard`。提交留本地。
- commit 只 `git add` 该 Task 涉及的文件，别带入 `.idea/`、`.gemini/`、`__pycache__`、`.xhx/`。
- **ruff B023 注意**：闭包里引用 `for` 循环变量（如 `turn`、`reg`）必须用默认参数绑定，例如 `def f(tc, turn=turn): ...`，否则 ruff 报 B023。

---

## 2. 实现前必读（先用读取工具读完这些，建立准确认知）
1. `src/xhx_agent/orchestrators/_toolturn.py` —— 共享的单工具执行 helper（Task 1 改这里）。
2. `src/xhx_agent/orchestrators/plan.py` —— tool-calling plan 编排器（Task 2 改这里）。当前已有 `run` / `_drive` / `_verify_and_repair` 三个方法。
3. `src/xhx_agent/orchestrators/loop.py` —— **只读，确认它如何调用 `execute_tool_call`**（应是 `def _run(tc, turn=turn): return execute_tool_call(ctx, tc, turn)`，解包 3 元组 `(tc, content, changed)`）。Task 1 之后**它必须一字不用改、且其测试全绿**。
4. `src/xhx_agent/safety/kernel.py` —— 看 `execute_tool(...)` 返回 `(result, trace, policy)`；`create_checkpoint(changed_files) -> Checkpoint`；`create_restore_plan(checkpoint)`。
5. `src/xhx_agent/evidence/store.py` —— `write_evidence(kind, source, summary, artifact_ref, confidence=0.8, ...) -> EvidenceEntry`（`.id`）；`write_trace(type, payload) -> RawTraceEntry`（`.id`）。
6. `src/xhx_agent/runtime/verify_loop.py` —— `checkpoint_path_value(workspace, run_id) -> Path`（相对路径）、`restore_plan_path_value(workspace, run_id) -> Path`、`_refresh_repo_intel_index(workspace, evidence, event_callback, risks)`。
7. **legacy 参照**：`src/xhx_agent/runtime/app.py` 里 `_run_model_tool_loop` 写 evidence + `patch_evidence_binding` 的那段（搜索 `patch_evidence_binding`），以及 `_execute_verification_and_repair_loop` 里 `create_checkpoint` / `create_restore_plan` 的用法。**照它的语义复刻**到 tool-calling plan。
8. `tests/test_plan_orchestrator.py` —— 看 3a 已有测试与 `_python_bug_workspace` helper（复制 `tests/fixtures/python_bug` fixture：`src/calc.py` 含 `return a - b  # TODO_BUG`，`tests/test_calc.py` 断言 `add(2,3)==5`）。新测试加在这里。

---

## Task 1：`_toolturn.py` 暴露富执行结果（`loop` 零改动）

**目的：** 让 plan 能拿到"工具结果的证据字段 + 工具调用 trace id"，从而写 evidence/binding。做法是加一个**富返回值**内部函数，`execute_tool_call` 变成丢弃 meta 的薄封装——**对外契约不变，loop 不用改**。

**Files:** Modify `src/xhx_agent/orchestrators/_toolturn.py`；Test：新增/扩 `tests/test_toolturn.py`（若不存在则新建）。

- [ ] **Step 1: 写失败测试** `tests/test_toolturn.py`（验证富版本带回 evidence meta；普通版本契约不变）。先 READ `_toolturn.py` 看现有 `execute_tool_call` 签名与 `OrchestratorContext` 如何构造（参考 `tests/test_loop_orchestrator.py` 里如何搭一个能跑工具的最小 ctx，或直接用 `RuntimeApp(...).run_task` 走通后从 trace 验证）。建议用一个**集成式**断言更稳：用 `_python_bug_workspace`（见 Task 2 helper）跑 `mode="plan"` 后，从 `.xhx/traces/*.jsonl` 读出是否有 `patch_evidence_binding`——但那属于 Task 2 行为。**本 Task 的单元测试**聚焦：
```python
def test_rich_returns_patch_evidence_meta(tmp_path):
    """apply_patch 经富执行后，meta 带回 evidence_kind='patch' 与 trace_id。"""
    # READ tests/test_loop_orchestrator.py 看如何构造最小可执行的 OrchestratorContext / kernel / tool_context。
    # 用一个能产生改动的 apply_patch ToolCall，调 _execute_tool_call_rich，断言：
    #   tc, content, changed, meta = _execute_tool_call_rich(ctx, tc, turn=1)
    #   assert changed == ["<被改文件>"]
    #   assert meta is not None and meta["evidence_kind"] == "patch" and meta["trace_id"]
```
> 若构造裸 ctx 成本高，可把本 Task 的测试并入 Task 2 的集成测试（即只在 Task 2 通过"trace 里有 patch_evidence_binding"间接验证 rich 生效），Task 1 仅保证 loop 回归全绿。两种都可接受，**但必须有测试覆盖"plan 产生 patch 证据 + binding"**（见 Task 2 Step 1）。

- [ ] **Step 2: 运行确认失败/或跳到 Task 2** —— `PYTHONUTF8=1 uv run pytest tests/test_toolturn.py -q`

- [ ] **Step 3: 改 `_toolturn.py`**。把现有 `execute_tool_call` 的主体抽成 `_execute_tool_call_rich`，返回 4 元组 `(tc, content, changed_files, meta)`；`execute_tool_call` 改成薄封装丢弃 meta（**保持对外 3 元组契约**）：
```python
def _execute_tool_call_rich(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str], dict | None]:
    """同 execute_tool_call，但额外带回 meta（结构化工具成功时含 evidence_kind/source/summary/trace_id；否则 None）。"""
    emit_event(ctx.event_callback, "tool_start", f"Tool execution started: {tc.name}", turn=turn, tool=tc.name)
    d = ctx.kernel.tool_registry.definition(tc.name)
    if d is not None and d.is_command:
        command = str(tc.arguments.get("command") or _default_verify_command(ctx.scan))
        try:
            exec_result = ctx.kernel.run_command_tool(
                command, evidence_kind="test" if tc.name == "verify" else "command",
                assume_yes=ctx.assume_yes, confirm_callback=ctx.confirm_callback,
                event_callback=ctx.event_callback, turn=turn)
            return tc, _render_tool_content(exec_result), list(exec_result.changed_files), None
        except Exception as exc:  # noqa: BLE001
            ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
            return tc, f"[{tc.name} error] {exc}", [], None
    step = ToolStep(tool=tc.name, arguments=tc.arguments)
    try:
        exec_result, trace, policy = ctx.kernel.execute_tool(ctx.tool_context, step, turn, ctx.event_callback)
        if exec_result is None:
            return tc, f"Tool denied/blocked: {policy.reason}", [], None
        meta = None
        if trace is not None and exec_result.evidence_kind and exec_result.evidence_source and exec_result.evidence_summary:
            meta = {
                "evidence_kind": exec_result.evidence_kind,
                "evidence_source": exec_result.evidence_source,
                "evidence_summary": exec_result.evidence_summary,
                "trace_id": trace.id,
            }
        return tc, _render_tool_content(exec_result), list(exec_result.changed_files), meta
    except Exception as exc:  # noqa: BLE001
        ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
        return tc, f"[{tc.name} error] {exc}", [], None


def execute_tool_call(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str]]:
    """对外契约不变（loop 用）：丢弃 meta，返回 3 元组。"""
    tc_, content, changed, _meta = _execute_tool_call_rich(ctx, tc, turn)
    return tc_, content, changed
```
> 字段名以 `ToolExecutionResult` 现状为准（先 READ `src/xhx_agent/tools/registry.py` 的 `ToolExecutionResult`，确认是 `evidence_kind/evidence_source/evidence_summary/changed_files`）。`execute_tool_call` 的对外签名/返回**绝不改**——这样 `loop.py` 不用动。

- [ ] **Step 4: 回归** —— `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py tests/test_toolturn.py -q`（loop 全绿）→ `PYTHONUTF8=1 uv run pytest -q`（零回归）。
- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/_toolturn.py tests/test_toolturn.py
git commit -m "refactor(orchestrators): rich tool-call result (evidence meta) without changing loop"
```

---

## Task 2：`plan.py` 写 evidence + patch-binding + checkpoint/restore

**Files:** Modify `src/xhx_agent/orchestrators/plan.py`；Test 扩 `tests/test_plan_orchestrator.py`。

### 2A. 工具结果写 evidence + patch_evidence_binding

- [ ] **Step 1: 写失败测试**（加到 `tests/test_plan_orchestrator.py`）：
```python
import json

def test_plan_writes_patch_evidence_and_binding(tmp_path, monkeypatch):
    workspace = _python_bug_workspace(tmp_path)  # 复用 3a helper
    _fake_chat_factory(monkeypatch, [_FIX_PATCH, None])  # 复用 3a：apply_patch 修好 -> done
    res = RuntimeApp(workspace).run_task("fix the failing test", profile_name="mock", mode="plan", assume_yes=True)
    assert res.status == "success" and "src/calc.py" in res.changed_files
    ev_files = list((workspace / ".xhx" / "evidence").glob("*.jsonl"))
    tr_files = list((workspace / ".xhx" / "traces").glob("*.jsonl"))
    assert ev_files and tr_files
    evidence = [json.loads(l) for l in ev_files[0].read_text(encoding="utf-8").splitlines() if l.strip()]
    traces = [json.loads(l) for l in tr_files[0].read_text(encoding="utf-8").splitlines() if l.strip()]
    patch_ev = next(e for e in evidence if e["kind"] == "patch")
    binding = next(t for t in traces if t["type"] == "patch_evidence_binding")
    assert binding["payload"]["evidence_id"] == patch_ev["id"]
    assert binding["payload"]["changed_files"] == ["src/calc.py"]
```

- [ ] **Step 2: 确认失败** —— `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py -q`

- [ ] **Step 3: 改 `plan.py` 的 `_drive`**：导入富版本（`from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, _execute_tool_call_rich`），把执行处的 `_run` 改为调用富版本，并在串行/并发**结果处理循环**里写 evidence + binding。即把现有：
```python
            def _run(tc, turn=turn):
                return execute_tool_call(ctx, tc, turn)
            ...
            for tc, content, changed in outcomes:
                emit_event(ctx.event_callback, "tool_result", "Tool execution completed.", turn=turn, tool=tc.name)
                changed_files.extend(changed)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
```
  改成（`_run` 返回 4 元组；循环解包 4 元组并写证据）：
```python
            def _run(tc, turn=turn):
                return _execute_tool_call_rich(ctx, tc, turn)
            ...
            for tc, content, changed, meta in outcomes:
                emit_event(ctx.event_callback, "tool_result", "Tool execution completed.", turn=turn, tool=tc.name)
                changed_files.extend(changed)
                if meta:
                    entry = ctx.evidence.write_evidence(
                        meta["evidence_kind"], meta["evidence_source"], meta["evidence_summary"],
                        f"trace://{meta['trace_id']}",
                        confidence=0.9 if meta["evidence_kind"] == "patch" else 0.8)
                    if meta["evidence_kind"] == "patch":
                        ctx.evidence.write_trace("patch_evidence_binding", {
                            "turn": turn, "tool_trace_id": meta["trace_id"],
                            "evidence_id": entry.id, "changed_files": list(changed)})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
```
  > `import` 处把原来的 `execute_tool_call` 换成 `_execute_tool_call_rich`（plan 不再用 3 元组版本）。`_render_tool_content` 仍由 `_execute_tool_call_rich` 内部使用，plan 无需直接导入它。确认并发分支（`all_readonly` 那段）的 `pool.map(_run, ...)` 也返回 4 元组——因为 `_run` 现在回 4 元组，循环统一解包即可。

- [ ] **Step 4: 回归** —— `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py -q` → 全量。

### 2B. checkpoint 前置 + 失败生成 restore plan + 暴露路径

- [ ] **Step 5: 写失败测试**（加到 `tests/test_plan_orchestrator.py`）：
```python
def test_plan_creates_checkpoint_and_restore_on_failure(tmp_path, monkeypatch):
    workspace = _python_bug_workspace(tmp_path)
    # 改成仍错（a*b）且不开 auto_repair：验证失败 -> 应生成 checkpoint + restore plan。
    _fake_chat_factory(monkeypatch, [_STILL_WRONG_PATCH, None])
    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True, auto_repair=False)
    assert res.verification == "failed"
    assert res.checkpoint_path is not None and (workspace / res.checkpoint_path).exists()
    assert res.restore_plan_path is not None and (workspace / res.restore_plan_path).exists()

def test_plan_checkpoint_on_success(tmp_path, monkeypatch):
    workspace = _python_bug_workspace(tmp_path)
    _fake_chat_factory(monkeypatch, [_FIX_PATCH, None])
    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True)
    assert res.verification == "passed"
    assert res.checkpoint_path is not None and (workspace / res.checkpoint_path).exists()
    assert res.restore_plan_path is None  # 成功不生成 restore plan
```
> `_STILL_WRONG_PATCH` / `_FIX_PATCH` 是 3a 已在该测试文件定义的常量，直接复用。

- [ ] **Step 6: 确认失败** —— `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py -q`

- [ ] **Step 7: 改 `plan.py` 的 `_verify_and_repair`**：在跑验证前拍 checkpoint，循环结束后若 `verification=="failed"` 生成 restore plan；把 `checkpoint_path` / `restore_plan_path` 透传出去到 `run()` 的 `write_report` 与 `RunResult`。
  - 顶部按需导入：`from xhx_agent.runtime.verify_loop import _refresh_repo_intel_index, checkpoint_path_value, restore_plan_path_value`（`_refresh_repo_intel_index` 可能已导入，去重）。
  - 在 `_verify_and_repair` 内，`_refresh_repo_intel_index(...)` 之后、`while True:` 之前初始化：`checkpoint = None`。
  - 在 `while True:` 体内、**确认 `vplan.commands` 非空后、跑验证命令之前**，拍 checkpoint：
```python
                checkpoint = ctx.kernel.create_checkpoint(sorted(set(changed_files)))
                emit_event(ctx.event_callback, "checkpoint", "Checkpoint created.",
                           checkpoint_id=checkpoint.id, changed_files=sorted(set(changed_files)))
```
  - `while` 结束后（return 之前），失败则生成 restore plan：
```python
        restore_plan_created = False
        if verification == "failed" and checkpoint is not None:
            ctx.kernel.create_restore_plan(checkpoint)
            restore_plan_created = True
            emit_event(ctx.event_callback, "restore_plan", "Restore plan created.", run_id=ctx.run_id)
```
  - `_verify_and_repair` 的返回值**增加两个**：`checkpoint_path` 与 `restore_plan_path`（字符串或 None），算法：
```python
        checkpoint_path = str(checkpoint_path_value(ctx.original_workspace, ctx.run_id)) if checkpoint is not None else None
        restore_plan_path = str(restore_plan_path_value(ctx.original_workspace, ctx.run_id)) if restore_plan_created else None
```
    把这两个值随原有返回元组一起带回（扩展返回签名；调用处 `run()` 同步解包）。
  - 在 `run()` 里，解包新增的 `checkpoint_path` / `restore_plan_path`，并传给 `write_report(..., checkpoint_path=checkpoint_path, restore_plan_path=restore_plan_path, ...)`（`write_report` 已支持这两个参数——先 READ `src/xhx_agent/evidence/report.py` 的 `write_report` 签名确认），以及 `RunResult(..., checkpoint_path=checkpoint_path, restore_plan_path=restore_plan_path)`。
  > **注意**：checkpoint 用 `ctx.kernel`（其 workspace 为活动工作区），路径值用 `ctx.original_workspace`——**与 legacy `app.py` 完全一致**（先 READ legacy 的 `checkpoint_path_value(ctx.original_workspace, ...)` 用法确认）。fixture 测试用的是非 git 工作区（就地执行，活动区==原始区），故路径可解析。

- [ ] **Step 8: 回归** —— `PYTHONUTF8=1 uv run pytest tests/test_plan_orchestrator.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（全量零回归；3a 既有 plan 测试不得破——它们不断言 checkpoint/restore 为 None，新增字段不影响）。
- [ ] **Step 9: 提交**
```bash
git add src/xhx_agent/orchestrators/plan.py tests/test_plan_orchestrator.py
git commit -m "feat(plan): evidence parity (patch evidence + binding, checkpoint/restore) for tool-calling plan"
```

---

## Task 3：收尾 —— ruff + 全量 + ROADMAP

- [ ] **Step 1: ruff** —— `PYTHONUTF8=1 uv run ruff check .`（必要时 `--fix`；保持全绿；当心 B023）。
- [ ] **Step 2: 全量** —— `PYTHONUTF8=1 uv run pytest -q`（全绿，零回归；基线 313 + 本切片新增测试）。
- [ ] **Step 3: ROADMAP** —— READ `ROADMAP.md`，在 Phase 3 区域加一行 `Phase 3b-1 ✅ 已完成（2026-06-11）`：给 tool-calling `plan` 补齐证据 parity（apply_patch 证据 + patch-evidence-binding + checkpoint/restore），纯增量、默认路由不变；默认切换 + 测试迁移留 3b-2。
- [ ] **Step 4: 提交**
```bash
git add ROADMAP.md docs/superpowers/plans/2026-06-11-phase3b-1-plan-evidence-parity.md
git commit -m "docs(roadmap): mark Phase 3b-1 (plan evidence parity) done"
```

> **不在本切片做（明确排除）：** ① 切换默认/`linear` 路由到 tool-calling plan；② 迁移 legacy 耦合测试；③ 改 `mock.py`；④ context-debug 报告 parity；⑤ 删除任何 legacy 代码（`_run_linear`/`_run_model_tool_loop`/ModelPlan/PlannerAgent/CoderAgent 全部保留）。这些都属于后续 3b-2。

---

## 自检（对照 3b-1 范围）
- ✅ tool-calling `plan` 的工具结果写 evidence 条目（patch=0.9 置信，其余 0.8）→ Task 2A
- ✅ `apply_patch` 写 `patch_evidence_binding` trace（绑定 trace_id ↔ evidence_id ↔ changed_files）→ Task 2A
- ✅ 验证前拍 checkpoint；整体失败生成 restore plan；`RunResult.checkpoint_path`/`restore_plan_path` 暴露 → Task 2B
- ✅ `loop.py` 外部行为零改动（只动 `_toolturn.py` 内部）→ Task 1
- ✅ 默认路由/现有测试/`mock.py` 不动；legacy 代码全保留 → 全程边界
