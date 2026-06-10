# Phase 2a：工具层加固 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 superpowers:executing-plans，逐任务执行。步骤用 `- [ ]`。

**Goal:** 清掉 Phase 1 终审的工具层欠债，把 `loop`/工具/策略层做扎实，为 Phase 2b（受控 terminal）打地基。

**Architecture:** 让 `ToolDefinition` 成为内置工具的单一来源（含 runner），参数校验由 JSON schema 派生（轻量自写校验器，不引 `jsonschema`）；`decide_tool` 的风险档由 `read_only/destructive` 标志推导（kernel 传入，避免 policy↔registry 循环依赖）；`loop` 的只读 tool_calls 并发执行；TUI 也渲染 `RunResult.answer`。

**Tech Stack:** Python 3.13、pydantic、pytest、uv。

**上位文档：** [ROADMAP](../../../ROADMAP.md) §3/§4/§8 · [经验文档](../../claude-code-learnings.md) §2 · Phase 1 终审遗留项。

**运行约定（Windows）：** 命令前置 `PYTHONUTF8=1`；测试 `uv run pytest -q`；lint `uv run ruff check .`。

**范围说明：** confirm 回路（confirm 档工具触发 confirm_callback）**不在本切片**——现设计 apply_patch 在 worktree 隔离下自动放行，confirm 真正有意义是 Phase 2b 的 terminal，届时随 terminal 一起做。

---

## 文件结构

- `src/xhx_agent/tools/registry.py` —— `ToolDefinition` 加 `runner`；runner 函数上移到 `TOOL_DEFINITIONS` 之前；`ToolRegistry` 增 `register_definition` / `definition()`；schema 派生校验替换 `_validate_arguments`
- `src/xhx_agent/safety/policy.py` —— `decide_tool` 按 `read_only/destructive` 推导风险
- `src/xhx_agent/safety/kernel.py` —— `execute_tool` 从 `tool_registry.definition()` 取标志传给 `decide_tool`
- `src/xhx_agent/orchestrators/loop.py` —— 一个 turn 内全只读的 tool_calls 并发执行
- `src/xhx_agent/tui/textual_app.py` —— `_apply_run_result` 渲染 `answer`
- 测试：新增/扩充 `tests/test_tool_registry.py`、`tests/test_safety.py`/`test_safety_kernel.py`、`tests/test_loop_orchestrator.py`、`tests/test_tui_textual.py`

---

## Task 1: `ToolDefinition` 纳入 runner + schema 派生校验（单一来源）

**Files:** Modify `src/xhx_agent/tools/registry.py`；Test `tests/test_tool_registry.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_tool_registry.py`）

```python
import pytest
from xhx_agent.models.types import ModelClientError, ModelPlan, ToolStep
from xhx_agent.tools.registry import default_tool_registry, TOOL_DEFINITIONS

def test_definitions_carry_runner():
    assert all(d.runner is not None for d in TOOL_DEFINITIONS.values())

def test_registry_definition_lookup():
    reg = default_tool_registry()
    assert reg.definition("read_file").read_only is True
    assert reg.definition("apply_patch").destructive is True
    assert reg.definition("nope") is None

def test_schema_validation_missing_required():
    reg = default_tool_registry()
    plan = ModelPlan(summary="s", status="continue", steps=[ToolStep(tool="read_file", arguments={})])
    with pytest.raises(ModelClientError) as ei:
        reg.validate_plan(plan)
    assert ei.value.code == "invalid_tool_arguments"

def test_schema_validation_wrong_type():
    reg = default_tool_registry()
    plan = ModelPlan(summary="s", status="continue", steps=[ToolStep(tool="search", arguments={"query": 123})])
    with pytest.raises(ModelClientError):
        reg.validate_plan(plan)

def test_schema_validation_ok():
    reg = default_tool_registry()
    plan = ModelPlan(summary="s", status="continue", steps=[ToolStep(tool="read_file", arguments={"path": "a.py"})])
    reg.validate_plan(plan)  # no raise
```

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_tool_registry.py -q`

- [ ] **Step 3: 重构 registry.py**

1. 在 `ToolDefinition` 加字段（放在 `destructive` 之后）：`runner: "ToolRunner | None" = None`。注意 `ToolRunner` 在文件后面定义——用字符串注解（文件已 `from __future__ import annotations`，所以直接 `runner: ToolRunner | None = None` 即可，无需引号）。
2. 把 `_run_search` / `_run_read_file` / `_run_apply_patch` 三个函数定义**移到 `TOOL_DEFINITIONS` 之前**（它们目前在文件末尾）。`ToolRunner` 类型别名也要在它们之前可用——把 `ToolRunner = Callable[...]` 一并上移到这三个函数之前。
3. 在每个 `ToolDefinition(...)` 末尾加 `runner=_run_search`（对应工具的 runner）。
4. `ToolRegistry`：保留 `_tools: dict[str, ToolRunner]`（兼容 mcp 动态注册），新增 `_definitions: dict[str, ToolDefinition] = {}`。
   - 新方法：
     ```python
     def register_definition(self, d: ToolDefinition) -> None:
         self._definitions[d.name] = d
         self._tools[d.name] = d.runner

     def definition(self, name: str) -> ToolDefinition | None:
         return self._definitions.get(name)
     ```
   - `tool_schemas()` 改为遍历 `self._definitions.values()`（不再依赖模块级 `TOOL_DEFINITIONS` 过滤）。
5. `validate_plan`：把 `self._validate_arguments(index, step)` 替换为：
     ```python
     d = self._definitions.get(step.tool)
     if d is not None:
         _validate_against_schema(index, step, d.parameters)
     # 无 definition 的动态工具（mcp_/custom_）跳过 schema 校验
     ```
   并新增模块级函数：
     ```python
     _JSON_PY_TYPES: dict[str, type | tuple[type, ...]] = {
         "string": str, "integer": int, "number": (int, float),
         "boolean": bool, "object": dict, "array": list,
     }

     def _validate_against_schema(index: int, step: ToolStep, schema: dict[str, Any]) -> None:
         props = schema.get("properties", {})
         required = schema.get("required", [])
         args = step.arguments
         for key in required:
             val = args.get(key)
             if val is None or (isinstance(val, str) and not val):
                 raise _invalid_tool_arguments(index, step, f"{step.tool} requires non-empty argument: {key}")
         for key, val in args.items():
             spec = props.get(key)
             if not spec or val is None:
                 continue
             py = _JSON_PY_TYPES.get(spec.get("type", ""))
             if py and not isinstance(val, py):
                 raise _invalid_tool_arguments(index, step, f"{step.tool} argument {key} must be {spec['type']}")
     ```
   删除旧的 `_validate_arguments` 方法。
6. `default_tool_registry()` 改为：
     ```python
     def default_tool_registry() -> ToolRegistry:
         registry = ToolRegistry()
         for d in TOOL_DEFINITIONS.values():
             registry.register_definition(d)
         return registry
     ```
   保留 `register(name, runner)` 方法不动（mcp.py 仍用它注册动态工具）。

- [ ] **Step 4: 运行确认通过** — `PYTHONUTF8=1 uv run pytest tests/test_tool_registry.py tests/test_tool_schemas.py -q`

- [ ] **Step 5: 全量回归** — `PYTHONUTF8=1 uv run pytest -q`（关注 test_safety_kernel / test_runtime / test_loop_orchestrator 仍过）

- [ ] **Step 6: 提交**
```bash
git add src/xhx_agent/tools/registry.py tests/test_tool_registry.py
git commit -m "refactor(tools): ToolDefinition carries runner; schema-derived arg validation"
```

> 实现前先 READ registry.py 现状，确认 `_tools`、`register`、`_validate_arguments`、三个 `_run_*` 与 `TOOL_DEFINITIONS` 的位置。mcp.py 用 `registry.register(name, runner)` 注册动态工具，**不要破坏该方法**。

---

## Task 2: `decide_tool` 按 read_only/destructive 推导风险

**Files:** Modify `src/xhx_agent/safety/policy.py`、`src/xhx_agent/safety/kernel.py`；Test `tests/test_safety.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_safety.py`）

```python
from xhx_agent.safety.policy import decide_tool
from xhx_agent.safety.risk import RiskLevel

def test_decide_tool_read_only_is_safe():
    d = decide_tool("read_file", read_only=True)
    assert d.decision == "allow" and d.risk is RiskLevel.SAFE

def test_decide_tool_destructive_is_confirm():
    d = decide_tool("apply_patch", destructive=True)
    assert d.decision == "allow" and d.risk is RiskLevel.CONFIRM

def test_decide_tool_dynamic_prefix_confirm():
    d = decide_tool("mcp_weather")
    assert d.decision == "allow" and d.risk is RiskLevel.CONFIRM

def test_decide_tool_unknown_denied():
    d = decide_tool("rm_everything")
    assert d.decision == "deny" and d.risk is RiskLevel.DENY
```

- [ ] **Step 2: 运行确认失败**（旧 `decide_tool` 无 `read_only`/`destructive` 参数 → TypeError）— `PYTHONUTF8=1 uv run pytest tests/test_safety.py -q`

- [ ] **Step 3: 改 `decide_tool`**（policy.py）替换整个函数：
```python
def decide_tool(tool_name: str, *, read_only: bool = False, destructive: bool = False) -> PolicyDecision:
    """按工具标志判定：只读→SAFE 放行；破坏性→CONFIRM 放行（worktree 隔离）；
    mcp_/custom_ 动态工具→CONFIRM 放行（以 Agent 权限运行、无沙箱）；其余拒绝。"""
    if read_only:
        return PolicyDecision(decision="allow", risk=RiskLevel.SAFE, reason=f"Tool {tool_name} is read-only.")
    if destructive:
        return PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM,
            reason=f"Tool {tool_name} performs writes; allowed under worktree isolation.")
    if tool_name.startswith("mcp_") or tool_name.startswith("custom_"):
        return PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM,
            reason=(f"Dynamic tool {tool_name} allowed; runs with the agent's own privileges "
                    "(no isolation sandbox), constrained only by the workspace boundary."))
    return PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=f"Tool {tool_name} is not allowed by policy.")
```

- [ ] **Step 4: 改 kernel.execute_tool**（kernel.py）把第一行 `policy = decide_tool(step.tool)` 改为从 registry 取标志：
```python
        d = self.tool_registry.definition(step.tool)
        policy = decide_tool(
            step.tool,
            read_only=bool(d and d.read_only),
            destructive=bool(d and d.destructive),
        )
```
（`self.tool_registry` 已有 `definition()` 方法——Task 1 新增。）

- [ ] **Step 5: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_safety.py tests/test_safety_kernel.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`
> 注意：旧 `decide_tool` 可能在别处被调用（grep `decide_tool(`）。若有其它调用点传的是裸 tool_name，行为会变（search/read_file 现在要靠 read_only=True 才 SAFE，否则落到 deny）。**确保所有调用点都经 kernel（已传标志）**；若有直接调 `decide_tool(name)` 的旧测试，按新语义更新断言。

- [ ] **Step 6: 提交**
```bash
git add src/xhx_agent/safety/policy.py src/xhx_agent/safety/kernel.py tests/test_safety.py
git commit -m "refactor(safety): derive decide_tool risk from read_only/destructive flags"
```

---

## Task 3: TUI 渲染 `RunResult.answer`

**Files:** Modify `src/xhx_agent/tui/textual_app.py`；Test `tests/test_tui_textual.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加；参照该文件现有用例的 app 构造方式）。断言：`_apply_run_result` 收到带 `answer` 的 `RunResult` 后，`answer` 文本进入 `self.messages`。
```python
def test_apply_run_result_surfaces_answer(tmp_path):
    from xhx_agent.runtime.app import RunResult
    app = _make_app(tmp_path)  # 复用本文件已有的 app 构造 helper；若无则按现有用例方式构造
    res = RunResult(run_id="r1", status="success", changed_files=[], commands=[],
                    verification="not_executed", summary_path="p", risk_summary=[], mode="loop",
                    answer="loop 的回答")
    app._apply_run_result(res)
    assert any("loop 的回答" in m for m in app.messages)
```
> 实现前 READ `tests/test_tui_textual.py` 看现有用例怎么构造/驱动 app（是否有 helper、是否需要 async/pilot）。按现有模式写，必要时调整。

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_tui_textual.py -q`

- [ ] **Step 3: 改 `_apply_run_result`**（textual_app.py，约 787 行）：
```python
    def _apply_run_result(self, result: RunResult) -> None:
        self.state.apply_result(result)
        if result.answer:
            self._append_message(f"assistant> {result.answer}")
        self.refresh_snapshot()
```
（`_append_message` 已存在，会 append 到 `self.messages` 并刷新。）

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_tui_textual.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/tui/textual_app.py tests/test_tui_textual.py
git commit -m "feat(tui): surface RunResult.answer in the textual conversation"
```

---

## Task 4: `loop` 只读 tool_calls 并发执行

**Files:** Modify `src/xhx_agent/orchestrators/loop.py`；Test `tests/test_loop_orchestrator.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加；用注入的 fake client 让一个 turn 返回两个 read_file tool_call，断言都被执行并各自得到 role:tool 结果，最终 answer 正常）

```python
def test_loop_runs_multiple_readonly_tools_in_one_turn(tmp_path, monkeypatch):
    from xhx_agent.models.types import ChatResult, ToolCall
    import xhx_agent.orchestrators.loop as loopmod
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    seq = [
        ChatResult(content=None, tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"path": "a.py"}),
            ToolCall(id="c2", name="read_file", arguments={"path": "b.py"})]),
        ChatResult(content="done", tool_calls=[]),
    ]
    class _Fake:
        def __init__(self): self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]; self.i += 1; return r
    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("read both", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "done"
```

- [ ] **Step 2: 运行确认通过或失败** — 顺序执行其实也能让此测试通过（它只验证正确性，不验证并发）。**因此本任务以"不破坏正确性"为主、并发为性能优化**。先运行确认顺序版已过：`PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q`

- [ ] **Step 3: 加并发**（loop.py）。在 `for tc in result.tool_calls:` 之前判断：若 `len(result.tool_calls) >= 2` 且**每个** tool_call 都是只读（`ctx.kernel.tool_registry.definition(tc.name)` 存在且 `.read_only`），则用线程池并发执行各 `kernel.execute_tool`，再**按原顺序**处理结果 + append role:tool 消息；否则维持现有顺序执行。把现有 per-tool 执行体抽成一个内部函数 `_exec_one(tc) -> (tc, content, changed)` 复用：
```python
            def _exec_one(tc):
                step = ToolStep(tool=tc.name, arguments=tc.arguments)
                try:
                    exec_result, _trace, policy = ctx.kernel.execute_tool(
                        ctx.tool_context, step, turn, ctx.event_callback)
                    if exec_result is None:
                        return tc, f"Tool denied/blocked: {policy.reason}", []
                    return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
                except Exception as exc:  # noqa: BLE001
                    ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
                    return tc, f"[{tc.name} error] {exc}", []

            reg = ctx.kernel.tool_registry
            all_readonly = len(result.tool_calls) >= 2 and all(
                (reg.definition(tc.name) is not None and reg.definition(tc.name).read_only)
                for tc in result.tool_calls)
            if all_readonly:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as ex:
                    outcomes = list(ex.map(_exec_one, result.tool_calls))
            else:
                outcomes = [_exec_one(tc) for tc in result.tool_calls]

            for tc, content, changed in outcomes:
                emit_event(ctx.event_callback, "tool_result", "Tool execution completed.", turn=turn, tool=tc.name)
                changed_files.extend(changed)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
```
（保留 `tool_start` 事件——可在 `_exec_one` 开头 emit，或在并发前对每个 tc emit 一次。把它放进 `_exec_one` 开头。）
> 实现时把 Task 7(Phase1) 已有的 try/except 逻辑迁进 `_exec_one`，保持"错误回喂、配对不丢"的不变式。`emit_event(... "tool_start" ...)` 放进 `_exec_one` 开头。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/loop.py tests/test_loop_orchestrator.py
git commit -m "feat(loop): execute read-only tool_calls in a turn concurrently"
```

---

## Task 5: 收尾 — ruff + ROADMAP 标记

- [ ] **Step 1: lint** — `PYTHONUTF8=1 uv run ruff check .`（有问题 `--fix`；E402/I001 等清干净）
- [ ] **Step 2: 全量** — `PYTHONUTF8=1 uv run pytest -q`（全绿）
- [ ] **Step 3: ROADMAP** 在"Phase 1 终审遗留（并入 Phase 2）"行后标注：①②③ 已完成（切片 2a），并把 Phase 2 行的 risk/confirm 注明"confirm 回路随 2b terminal"。
- [ ] **Step 4: 提交**
```bash
git add ROADMAP.md
git commit -m "docs(roadmap): mark Phase 2a (tool-layer hardening) done"
```

---

## 自检（对照 2a 范围）
- ✅ 遗留②（schema 单一来源 + runner）→ Task 1
- ✅ 遗留③（read_only/destructive → 风险门控）→ Task 2
- ✅ 遗留①（TUI 渲染 answer）→ Task 3
- ✅ 只读并发 → Task 4
- ⏸ confirm 回路 → 显式推迟到 Phase 2b（terminal 落地时）
