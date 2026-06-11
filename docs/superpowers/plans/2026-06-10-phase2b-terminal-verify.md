# Phase 2b：受控 terminal/bash + verify 工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development，逐任务执行。步骤用 `- [ ]`。

**Goal:** 让 `loop` 的模型能调用 `terminal`（跑任意 shell 命令）和 `verify`（跑测试），每条命令都过 `decide_terminal` 命令级风险闸门（SAFE 自动跑 / CONFIRM 弹确认 / DENY 拦截）+ 120s 看门狗。这是 Phase 2 的招牌：把"模型跑命令"安全地接进统一闸门。

**Architecture:** `terminal`/`verify` 是"命令工具"（`ToolDefinition.is_command=True`），与结构化工具（read_file/search/apply_patch）的工具名级门控不同——它们走命令级门控。`loop._exec_one` 把命令工具路由到新的 `kernel.run_command_tool`（内部 `run_terminal` = `decide_terminal` + `confirm_callback` + 看门狗），带上 `ctx.assume_yes/confirm_callback/event_callback`；confirm 回路在此真正落地。

**Tech Stack:** Python 3.13、pydantic、pytest、uv。

**上位文档：** [ROADMAP](../../../ROADMAP.md) §8 · Phase 2a（`ToolDefinition`/`definition()`/risk-from-flags 已就位）。

**运行约定（Windows）：** 命令前置 `PYTHONUTF8=1`；测试 `uv run pytest -q`；lint `uv run ruff check .`。

**已知的风险分级（来自 `safety/risk.py`，写测试用）：**
- SAFE（自动跑）：`pwd` `ls` `dir` `rg` `cat` `type` `git status` `git diff`。
- CONFIRM（需确认）：`pytest` `python -m pytest` `uv run pytest` `npm test` `npm run build/typecheck`；以及一切未知命令（兜底）。
- DENY（拦截）：shell 元字符（`;|&`>`` 等）、黑名单可执行（`rm`/`curl`/`bash`/`sudo`/`mv`…）、内联代码（`python -c`）、危险 git（`reset --hard`/`push --force`/`clean -f`/`checkout .`）。

---

## 文件结构
- `src/xhx_agent/tools/registry.py` —— `ToolDefinition` 加 `is_command`；新增 `terminal`/`verify` 两个 `ToolDefinition`（无 runner——命令工具不走 runner）
- `src/xhx_agent/safety/kernel.py` —— 新增 `run_command_tool(...)`
- `src/xhx_agent/orchestrators/loop.py` —— `_exec_one` 路由命令工具
- 测试：`tests/test_tool_schemas.py`、`tests/test_safety_kernel.py`、`tests/test_loop_orchestrator.py`

---

## Task 1: `ToolDefinition.is_command` + `terminal`/`verify` 定义

**Files:** Modify `src/xhx_agent/tools/registry.py`；Test `tests/test_tool_schemas.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加）
```python
def test_terminal_and_verify_in_schemas():
    from xhx_agent.tools.registry import default_tool_registry
    names = {s["function"]["name"] for s in default_tool_registry().tool_schemas()}
    assert {"terminal", "verify"} <= names

def test_command_tools_flagged():
    from xhx_agent.tools.registry import default_tool_registry
    reg = default_tool_registry()
    assert reg.definition("terminal").is_command is True
    assert reg.definition("verify").is_command is True
    assert reg.definition("read_file").is_command is False
```

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_tool_schemas.py -q`

- [ ] **Step 3: 改 registry.py**
1. `ToolDefinition` 加字段 `is_command: bool = False`（放在 `destructive` 之后）。`runner` 仍可为 None。
2. 在 `TOOL_DEFINITIONS` 里加两条（runner=None，因为命令工具不走结构化 runner；它们由 loop 路由到 `run_command_tool`）：
```python
    "terminal": ToolDefinition(
        name="terminal",
        description=("在仓库工作区运行一条 shell 命令并返回输出。命令会过安全风险分级："
                     "只读命令(ls/cat/git status 等)自动执行；测试等命令需用户确认；危险命令(rm/curl/bash/sudo/重定向等)被拒。"),
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的完整命令（单条，不要用 ; | & 等拼接）"}},
            "required": ["command"]},
        is_command=True),
    "verify": ToolDefinition(
        name="verify",
        description="运行项目测试做验证。可选 command（默认按项目语言推断，如 python -m pytest）。",
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "可选：自定义验证命令；省略则用项目默认测试命令"}},
            "required": []},
        is_command=True),
```
3. `register_definition` 已会把 `d.name → d.runner`（None）放进 `_tools`；这没问题，但**命令工具不应被 `execute()` 当结构化工具跑**。为安全：在 `register_definition` 里，仅当 `d.runner is not None` 时才写 `self._tools[d.name] = d.runner`；`_definitions[d.name] = d` 始终写。这样 `terminal`/`verify` 进 `_definitions`（→ schema、definition() 可查），但不进 `_tools`（→ 不会被结构化 `execute` 误跑）。
   ```python
   def register_definition(self, d: ToolDefinition) -> None:
       self._definitions[d.name] = d
       if d.runner is not None:
           self._tools[d.name] = d.runner
   ```

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_tool_schemas.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（关注 test_tool_registry / test_safety_kernel 仍过——注意 `_tools` 现在不含 terminal/verify）

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/tools/registry.py tests/test_tool_schemas.py
git commit -m "feat(tools): add terminal/verify command-tool definitions (is_command)"
```

> 实现前 READ registry.py 确认 Phase 2a 后的 `register_definition`/`_definitions`/`_tools`/`tool_schemas`/`definition` 形态。

---

## Task 2: `kernel.run_command_tool`

**Files:** Modify `src/xhx_agent/safety/kernel.py`；Test `tests/test_safety_kernel.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加；用 SAFE/DENY 命令真实驱动，CONFIRM 用回调）
```python
def _kernel(tmp_path):
    from xhx_agent.evidence.store import EvidenceStore
    from xhx_agent.safety.kernel import SafeExecutionKernel
    from xhx_agent.tools.registry import default_tool_registry
    ev = EvidenceStore(tmp_path, "run-x")
    return SafeExecutionKernel(tmp_path, "run-x", ev, default_tool_registry())

def test_run_command_tool_safe_runs(tmp_path):
    k = _kernel(tmp_path)
    r = k.run_command_tool("git status", evidence_kind="command", assume_yes=False, confirm_callback=None)
    assert r.tool == "terminal" and r.status in ("success", "failed")  # 真实跑（非 git 仓也会返回，但不崩）

def test_run_command_tool_deny_blocked(tmp_path):
    k = _kernel(tmp_path)
    r = k.run_command_tool("rm -rf x", evidence_kind="command", assume_yes=False, confirm_callback=None)
    assert r.status == "deny"

def test_run_command_tool_confirm_declined(tmp_path):
    k = _kernel(tmp_path)
    r = k.run_command_tool("pytest", evidence_kind="test", assume_yes=False, confirm_callback=lambda c, p: False)
    assert r.status == "confirm"
```
> READ `tests/test_safety_kernel.py` 看现有 kernel 测试怎么构造 `SafeExecutionKernel`/`EvidenceStore`，照它的方式构造（上面的 `_kernel` 可能需按现有 import 路径微调）。

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_safety_kernel.py -q`

- [ ] **Step 3: 在 kernel.py 加方法**（`run_verification` 旁边；`run_terminal` 已 import）：
```python
    def run_command_tool(
        self,
        command: str,
        *,
        evidence_kind: str = "command",
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        event_callback: EventCallback | None = None,
        turn: int = 0,
    ) -> ToolExecutionResult:
        """命令工具（terminal/verify）的执行入口：过 decide_terminal 命令级闸门 + confirm，跑命令，转成 ToolExecutionResult。"""
        result = run_terminal(self.workspace, command, assume_yes=assume_yes, confirm_callback=confirm_callback)
        self.record_policy("terminal", command, result.policy, {"turn": turn, "command": command}, event_callback)
        self.evidence.write_trace("tool_result", {"turn": turn, "tool": "terminal", "command": command, **result.model_dump()})
        ok = result.status == "success"
        return ToolExecutionResult(
            tool="terminal",
            status=result.status,
            summary=result.summary or f"command {result.status}",
            trace_payload={"tool": "terminal", "command": command, **result.model_dump()},
            evidence_kind=evidence_kind if ok else None,
            evidence_source=command if ok else None,
            evidence_summary=result.summary if ok else None,
            error=None if ok else (result.stderr or result.summary or result.status),
        )
```
确认 kernel.py 顶部已 import `run_terminal`、`ToolExecutionResult`、`ConfirmationCallback`、`EventCallback`（`run_terminal` 在 `run_verification` 里用到，应已 import；`ToolExecutionResult` 从 `tools.registry`；`ConfirmationCallback` 从 `tools.terminal`）。缺的补上。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_safety_kernel.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/safety/kernel.py tests/test_safety_kernel.py
git commit -m "feat(safety): kernel.run_command_tool for gated terminal/verify execution"
```

---

## Task 3: `loop._exec_one` 路由命令工具

**Files:** Modify `src/xhx_agent/orchestrators/loop.py`；Test `tests/test_loop_orchestrator.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加；fake client 让模型调 terminal 跑 SAFE 命令，断言成功并最终回答）
```python
def test_loop_terminal_tool_runs_safe_command(tmp_path, monkeypatch):
    from xhx_agent.models.types import ChatResult, ToolCall
    import xhx_agent.orchestrators.loop as loopmod
    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="terminal", arguments={"command": "git status"})]),
        ChatResult(content="checked", tool_calls=[]),
    ]
    class _Fake:
        def __init__(self): self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]; self.i += 1; return r
    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("check status", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "checked"

def test_loop_terminal_deny_is_fed_back(tmp_path, monkeypatch):
    from xhx_agent.models.types import ChatResult, ToolCall
    import xhx_agent.orchestrators.loop as loopmod
    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="terminal", arguments={"command": "rm -rf src"})]),
        ChatResult(content="ok", tool_calls=[]),
    ]
    class _Fake:
        def __init__(self): self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]; self.i += 1; return r
    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("delete", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "ok"  # deny 被回喂、不崩，模型继续
```

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q`（terminal 工具尚未路由 → 走结构化 execute → unsupported/失败）

- [ ] **Step 3: 改 `loop.py` 的 `_exec_one`**：在函数开头、构造 `step` 之前，判断命令工具并路由：
```python
            def _exec_one(tc, turn=turn):
                emit_event(ctx.event_callback, "tool_start", f"Tool execution started: {tc.name}",
                           turn=turn, tool=tc.name)
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
                    exec_result, _trace, policy = ctx.kernel.execute_tool(
                        ctx.tool_context, step, turn, ctx.event_callback)
                    if exec_result is None:
                        return tc, f"Tool denied/blocked: {policy.reason}", []
                    return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
                except Exception as exc:  # noqa: BLE001
                    ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
                    return tc, f"[{tc.name} error] {exc}", []
```
并在 loop.py 模块级加一个默认验证命令的辅助：
```python
def _default_verify_command(scan: Any) -> str:
    langs = getattr(scan, "detected_languages", []) or []
    if "python" in langs:
        return "python -m pytest"
    if "javascript" in langs or "typescript" in langs:
        return "npm test"
    return "python -m pytest"
```
> 注意：命令工具的 `definition().read_only` 为 False，所以 Task 2a 的"全只读才并发"判断**不会**把它们并发——命令/写操作仍串行，符合预期。`_render_tool_content` 对命令结果：status≠success 时返回 `[terminal failed] ...`，模型能看到被拒/失败原因。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（malformed/denied/concurrency 等既有 loop 测试必须仍过）

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/loop.py tests/test_loop_orchestrator.py
git commit -m "feat(loop): route terminal/verify command tools through the safety gate"
```

---

## Task 4: 收尾 — ruff + 真实联调 + ROADMAP

- [ ] **Step 1: ruff** — `PYTHONUTF8=1 uv run ruff check .`（`--fix` 清理；E702/I001/B023 等清干净）
- [ ] **Step 2: 全量** — `PYTHONUTF8=1 uv run pytest -q`（全绿）
- [ ] **Step 3: 真实联调（手动，需 DeepSeek key；由协调者执行，不在 subagent 内）** —— 跑一个让模型用 terminal 的任务，确认 SAFE 命令真实执行、危险命令被拒。
- [ ] **Step 4: ROADMAP** 标记 Phase 2b 完成（terminal/verify + confirm 回路），并把 Phase 2 行的 confirm 注明已落地。
- [ ] **Step 5: 提交**
```bash
git add ROADMAP.md
git commit -m "docs(roadmap): mark Phase 2b (controlled terminal/verify) done"
```

---

## 自检（对照 2b 范围）
- ✅ 受控 `terminal` 工具（命令级门控 + 看门狗）→ Task 1-3
- ✅ `verify` 工具（默认项目测试命令）→ Task 1-3
- ✅ confirm 回路落地（CONFIRM 档命令经 confirm_callback）→ Task 2-3
- ✅ 命令工具不并发（串行）→ 复用 2a 的 read_only 判断（命令工具非只读）
