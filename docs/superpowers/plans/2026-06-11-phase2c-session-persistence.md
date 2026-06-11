# Phase 2c：会话持久化（完整消息历史） 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development，逐任务执行。步骤用 `- [ ]`。

**Goal:** 把 `loop` 范式的会话从"**摘要续接**"升级为"**完整消息历史持久化**"。每次 `loop` 运行结束，把完整对话历史 H（system 之外的 user/assistant/tool 全序列，**含最终 assistant 回答**）落盘到 `.xhx/sessions/<run_id>.json`；`--continue`/`--resume` 时把整段历史读回、还原成真实对话上下文继续跑，而不是只塞一段摘要。

**Architecture:** 三层职责分明：
- **持久层**（`runtime/session.py`）：`SessionEntry` 仍是 `history.jsonl` 里的一行索引，新增 `transcript_path`（指向整段历史 JSON）+ `mode`（续接时复用同一范式）。新增 `save_transcript` / `load_transcript` / `transcript_path` 三个函数。
- **编排层**（`orchestrators/loop.py` + `orchestrators/base.py`）：`OrchestratorContext` 加 `prior_messages`；`LoopOrchestrator.run` 起手用 `prior_messages` 重建对话（新 system + 历史去旧 system + 新 user task），结束时存整段 transcript 并把相对路径写进 `RunResult.transcript_path`。**修一个既有缺陷**：当前 loop 在模型回纯文本（answer）时直接 break，没把这条最终 assistant 消息 append 进 `messages`，导致 transcript 缺最后一句——存盘前补上。
- **入口层**（`runtime/app.py` + `cli/main.py`）：`run_task` 加 `prior_messages` 形参透传进 ctx；CLI `--continue`/`--resume` 优先全量还原（有 transcript 就读回传 `prior_messages`、`task` 保持纯净、`mode` 复用 `entry.mode`），缺 transcript（老会话）才回退现有 `format_follow_up` 摘要拼接。

**与长期记忆（§7）分工：** 会话 = 单次 run 的完整状态（这一期）；记忆 = 跨会话事实（Phase 6）。两者各管一摊，互不替代。

**Tech Stack:** Python 3.13、pydantic、pytest、uv。

**上位文档：** [ROADMAP](../../../ROADMAP.md) §3 改造（会话管理升级）+ §4 Phase 2。

**运行约定（Windows）：** 命令前置 `PYTHONUTF8=1`；测试 `uv run pytest -q`；lint `uv run ruff check .`。

---

## 文件结构
- `src/xhx_agent/runtime/session.py` —— `SessionEntry` 加 `transcript_path`/`mode`；新增 `transcript_path()`/`save_transcript()`/`load_transcript()`；`record_session` 落 transcript 并回填两字段
- `src/xhx_agent/orchestrators/base.py` —— `OrchestratorContext` 加 `prior_messages: list[dict] | None = None`
- `src/xhx_agent/orchestrators/loop.py` —— 起手 seed `prior_messages`；最终 answer 也 append 进 messages；结束存 transcript + 设 `RunResult.transcript_path`
- `src/xhx_agent/runtime/app.py` —— `RunResult` 加 `transcript_path: str | None = None`；`run_task` 加 `prior_messages` 形参 → ctx
- `src/xhx_agent/cli/main.py` —— `--continue`/`--resume` 全量还原优先、摘要回退
- 测试：`tests/test_session.py`、`tests/test_loop_orchestrator.py`

---

## Task 1：持久层 —— transcript 存取 + SessionEntry 字段 + record_session 回填

**Files:** Modify `src/xhx_agent/runtime/session.py`；Test `tests/test_session.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_session.py`；`_ResultStub` 不含新字段，验证向后兼容）
```python
def test_transcript_roundtrip(tmp_path) -> None:
    from xhx_agent.runtime.session import load_transcript_messages, save_transcript
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    rel = save_transcript(tmp_path, "run-7", msgs)
    assert rel.endswith("run-7.json")
    assert load_transcript_messages(tmp_path, rel) == msgs


def test_load_transcript_missing_returns_none(tmp_path) -> None:
    from xhx_agent.runtime.session import load_transcript_messages
    assert load_transcript_messages(tmp_path, ".xhx/sessions/nope.json") is None
    assert load_transcript_messages(tmp_path, None) is None


def test_record_session_persists_transcript_and_mode(tmp_path) -> None:
    class _LoopResult:
        run_id = "run-8"
        status = "success"
        verification = "not_executed"
        changed_files = ["a.py"]
        summary_path = "r.md"
        mode = "loop"
        transcript_path = None  # loop 已自存；record 也兜底处理见下
        messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]

    entry = record_session(tmp_path, "do it", _LoopResult())
    assert entry.mode == "loop"
    assert entry.transcript_path is not None
    from xhx_agent.runtime.session import load_transcript_messages
    assert load_transcript_messages(tmp_path, entry.transcript_path) == _LoopResult.messages


def test_record_session_backward_compatible_stub(tmp_path) -> None:
    # 老式 stub（无 mode/messages/transcript_path）不应报错，transcript_path 留空
    record_session(tmp_path, "x", _ResultStub("run-9", "success", "passed", [], None))
    entry = load_latest_session(tmp_path)
    assert entry is not None and entry.transcript_path is None and entry.mode == ""
```
> READ `tests/test_session.py` 顶部，复用已有的 `_ResultStub`、`record_session`、`load_latest_session` import。

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_session.py -q`

- [ ] **Step 3: 改 `session.py`**
1. `SessionEntry` 在 `summary_path` 后加两字段：
```python
    transcript_path: str | None = None
    mode: str = ""
```
2. 新增 transcript 路径与存取函数（放在 `session_history_path` 附近）：
```python
import json  # 顶部若未引入则补


def transcript_path(workspace: Path, run_id: str) -> Path:
    return xhx_dir(workspace) / "sessions" / f"{run_id}.json"


def save_transcript(workspace: Path, run_id: str, messages: list[dict]) -> str:
    """落盘整段消息历史，返回相对 workspace 的 POSIX 路径（写进索引/RunResult）。"""
    ensure_xhx_dirs(workspace)
    path = transcript_path(workspace, run_id)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.relative_to(workspace).as_posix()


def load_transcript_messages(workspace: Path, rel_path: str | None) -> list[dict] | None:
    """按相对路径读回整段历史；缺文件/空路径返回 None（让上层回退摘要续接）。"""
    if not rel_path:
        return None
    path = workspace / rel_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
```
3. `record_session` 改为：先（若 result 带 messages 且尚无 transcript_path）落 transcript，再把 `transcript_path`/`mode` 写进 entry。用 `getattr` 保持对老式 stub 的向后兼容：
```python
def record_session(workspace: Path, task: str, result: RunResult) -> SessionEntry:
    """Append a one-line summary of ``result`` to the session history (+ persist full transcript when present)."""

    ensure_xhx_dirs(workspace)
    rel_transcript = getattr(result, "transcript_path", None)
    messages = getattr(result, "messages", None)
    if rel_transcript is None and messages:
        rel_transcript = save_transcript(workspace, result.run_id, messages)
    entry = SessionEntry(
        run_id=result.run_id,
        task=task,
        status=result.status,
        verification=result.verification,
        changed_files=list(result.changed_files),
        summary_path=result.summary_path,
        transcript_path=rel_transcript,
        mode=getattr(result, "mode", "") or "",
    )
    with session_history_path(workspace).open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")
    return entry
```
> 说明：loop 会自己存 transcript 并把相对路径放进 `RunResult.transcript_path`（Task 2），所以正常路径下 `rel_transcript` 已非 None、不会重复存；`messages` 兜底分支是给"有 messages 但没自存"的调用方（如测试 stub）留的。两者只触发其一。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_session.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（现有 4 个 session 测试必须仍过——新字段都有默认值，`format_follow_up` 不受影响）

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/runtime/session.py tests/test_session.py
git commit -m "feat(session): persist full transcript (.xhx/sessions/<run_id>.json) + index fields"
```

---

## Task 2：loop 存整段 transcript（含最终 answer）+ RunResult.transcript_path

**Files:** Modify `src/xhx_agent/orchestrators/loop.py`、`src/xhx_agent/runtime/app.py`；Test `tests/test_loop_orchestrator.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加；断言 transcript 落盘、含 user+assistant 最终回答）
```python
def test_loop_persists_full_transcript(tmp_path, monkeypatch):
    import json
    from xhx_agent.models.types import ChatResult, ToolCall
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.runtime.app import RuntimeApp
    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="read_file",
                   arguments={"path": "README.md"})]),
        ChatResult(content="done reading", tool_calls=[]),
    ]
    class _Fake:
        def __init__(self): self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]; self.i += 1; return r
    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("read it", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "done reading"
    assert res.transcript_path is not None
    saved = json.loads((tmp_path / res.transcript_path).read_text(encoding="utf-8"))
    roles = [m["role"] for m in saved]
    assert roles[0] == "system" and "user" in roles and "tool" in roles
    # 最终 assistant 回答必须在历史里（修复"漏存最后一句"）
    assert saved[-1] == {"role": "assistant", "content": "done reading"}
```
> READ `tests/test_loop_orchestrator.py` 看既有 fake client / `run_task` 调用风格，对齐（上面的 `_Fake` 若与既有 helper 重复可复用既有的）。`read_file` 对不存在文件会失败但不崩，会作为 role:tool 错误消息进历史——满足断言（有 tool 角色）。若担心，把工具换成确定存在的文件或 `search`。

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q`（`RunResult` 还没 `transcript_path` 字段 → AttributeError/校验失败）

- [ ] **Step 3a: `RunResult` 加字段**（`src/xhx_agent/runtime/app.py`，`answer` 旁）：
```python
    answer: str | None = None
    transcript_path: str | None = None
```

- [ ] **Step 3b: 改 `loop.py`**
1. 顶部 import：`from xhx_agent.runtime.session import save_transcript`。
2. 在"模型回纯文本即结束"分支里，**把最终 assistant 消息也 append 进 messages** 再 break：
```python
            if not result.tool_calls:
                answer = result.content or ""
                messages.append({"role": "assistant", "content": answer})
                emit_event(ctx.event_callback, "model_plan", f"loop answer [turn {turn}]",
                           turn=turn, step_count=0, status="done")
                break
```
3. 在 `write_report(...)` 之后、`return RunResult(...)` 之前，存 transcript：
```python
        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)
```
4. `return RunResult(...)` 末尾加 `transcript_path=transcript_rel`。
> 注意：transcript 存到 `ctx.original_workspace`（主工作区，worktree 清理后仍在），与 `write_report` 一致。`messages` 里 tool 结果已被截到 `_MAX_TOOL_RESULT_CHARS`，体积可控。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（既有 loop 测试断言 `res.answer` 等仍过；新增 assistant append 不影响 answer 值）

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/loop.py src/xhx_agent/runtime/app.py tests/test_loop_orchestrator.py
git commit -m "feat(loop): persist full message history incl. final answer; RunResult.transcript_path"
```

---

## Task 3：还原路径 —— prior_messages 贯通 ctx/loop/run_task + CLI 全量续接

**Files:** Modify `src/xhx_agent/orchestrators/base.py`、`src/xhx_agent/orchestrators/loop.py`、`src/xhx_agent/runtime/app.py`、`src/xhx_agent/cli/main.py`；Test `tests/test_loop_orchestrator.py`（扩充）

- [ ] **Step 1: 写失败测试**（追加；prior_messages 注入后，fake client 应在首轮就看到历史里的旧 user 消息）
```python
def test_loop_restores_prior_messages(tmp_path, monkeypatch):
    from xhx_agent.models.types import ChatResult
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.runtime.app import RuntimeApp
    seen = {}
    class _Fake:
        def chat(self, messages, tools):
            seen["roles"] = [m["role"] for m in messages]
            seen["contents"] = [m.get("content") for m in messages]
            return ChatResult(content="continued", tool_calls=[])
    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    prior = [
        {"role": "system", "content": "OLD SYSTEM — must be dropped"},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    res = RuntimeApp(tmp_path).run_task(
        "follow up", profile_name="mock", mode="loop", prior_messages=prior)
    assert res.status == "success" and res.answer == "continued"
    # 恰好一个 system（新的），旧 system 被丢弃；历史 user/assistant 在；新 task 在末尾
    assert seen["roles"].count("system") == 1
    assert "OLD SYSTEM — must be dropped" not in seen["contents"]
    assert "earlier question" in seen["contents"]
    assert seen["roles"][-1] == "user" and seen["contents"][-1] == "follow up"
```

- [ ] **Step 2: 运行确认失败** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q`（`run_task` 还没 `prior_messages` 形参 → TypeError）

- [ ] **Step 3a: `OrchestratorContext` 加字段**（`orchestrators/base.py`，放在 `event_callback` 之后、`metrics_tracker` 之前或之后均可，注意 dataclass 默认值字段都在末尾）：
```python
    prior_messages: list[dict] | None = None
```

- [ ] **Step 3b: `loop.py` 起手 seed**：把现有
```python
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": LOOP_SYSTEM_PROMPT + "\n\n" + render_xhx_md(ctx.scan)},
            {"role": "user", "content": ctx.task},
        ]
```
改成：先放新 system，再把 `prior_messages` 里**非 system** 的历史原样接上，最后放新 user task：
```python
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": LOOP_SYSTEM_PROMPT + "\n\n" + render_xhx_md(ctx.scan)},
        ]
        if ctx.prior_messages:
            messages.extend(m for m in ctx.prior_messages if m.get("role") != "system")
        messages.append({"role": "user", "content": ctx.task})
```
> 保留旧历史里的 assistant(tool_calls) 与其后的 role:tool 配对——原样 extend 即可，顺序与 tool_call_id 配对天然正确。只剔除旧 system，避免双 system。

- [ ] **Step 3c: `run_task` 加形参 → ctx**（`runtime/app.py`）：
  - 签名加 `prior_messages: list[dict] | None = None,`（放在 `mode` 旁）。
  - 构造 `OrchestratorContext(...)` 时加 `prior_messages=prior_messages,`。

- [ ] **Step 3d: CLI `--continue`/`--resume` 全量还原优先**（`cli/main.py`，替换现有 `if cont or resume:` 块）：
```python
    effective_task = task
    prior_messages = None
    resume_mode = mode
    if cont or resume:
        previous = load_latest_session(runtime.workspace) if cont else load_session(runtime.workspace, resume or "")
        if previous is not None:
            restored = load_transcript_messages(runtime.workspace, previous.transcript_path)
            verb = "Continuing" if cont else "Resuming"
            if restored:
                prior_messages = restored
                resume_mode = mode or (previous.mode or None)
                console.print(f"{verb} from run {previous.run_id} ({previous.status}) — full transcript restored.")
            else:
                effective_task = format_follow_up(previous) + "\n\n" + task
                console.print(f"{verb} from run {previous.run_id} ({previous.status}) — summary only.")
        else:
            target = "most recent session" if cont else f"session '{resume}'"
            console.print(f"No {target} found; starting fresh.")
```
  - 顶部 import 补 `load_transcript_messages`（与现有 `from xhx_agent.runtime.session import (...)` 同处加入）。
  - 两处 `runtime.run_task(...)` 调用都把 `mode=mode` 改为 `mode=resume_mode`，并加 `prior_messages=prior_messages`。例如非 json 分支：
```python
    result = runtime.run_task(
        effective_task,
        profile,
        assume_yes=yes,
        confirm_callback=_confirm_terminal_command,
        auto_repair=auto_repair,
        mode=resume_mode,
        prior_messages=prior_messages,
    )
```
  - json 分支同理（`runtime.run_task(effective_task, profile, assume_yes=yes, auto_repair=auto_repair, mode=resume_mode, prior_messages=prior_messages)`）。
> 全量还原时 `effective_task` 保持纯净的新 `task`（不再前置摘要），历史由 `prior_messages` 提供；`resume_mode` 复用上次范式（多为 loop），保证续接走 loop 编排器（只有 loop 读 `prior_messages`，其余编排器忽略该字段、无副作用）。

- [ ] **Step 4: 运行 + 回归** — `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py tests/test_session.py -q` 然后 `PYTHONUTF8=1 uv run pytest -q`（既有 CLI/编排器测试全过；非 loop 编排器拿到 `prior_messages` 也不应报错——它们根本不读）

- [ ] **Step 5: 提交**
```bash
git add src/xhx_agent/orchestrators/base.py src/xhx_agent/orchestrators/loop.py src/xhx_agent/runtime/app.py src/xhx_agent/cli/main.py tests/test_loop_orchestrator.py
git commit -m "feat(session): restore full conversation on --continue/--resume (prior_messages)"
```

---

## Task 4：收尾 —— ruff + 全量 + 真实联调 + ROADMAP

- [ ] **Step 1: ruff** — `PYTHONUTF8=1 uv run ruff check .`（`--fix` 清 I001/E501 等；保持全绿）
- [ ] **Step 2: 全量** — `PYTHONUTF8=1 uv run pytest -q`（全绿，无回归）
- [ ] **Step 3: 真实联调（手动，需 DeepSeek key；由协调者执行，不在 subagent 内）** —— 跑一次 `loop` 对话，结束后 `xhx sessions` 看到带 transcript 的条目，再 `xhx run "继续刚才的话题…" --continue` 确认模型能接上整段对话（而非只看到摘要）。
- [ ] **Step 4: ROADMAP** 标记 Phase 2c 完成：在 §4 Phase 2 行补注"会话持久化已落地"，并在 §3 改造里把"会话管理"那条标 ✅；可加一行 `Phase 2c ✅ 已完成（2026-06-11）` 简述。
- [ ] **Step 5: 提交**
```bash
git add ROADMAP.md docs/superpowers/plans/2026-06-11-phase2c-session-persistence.md
git commit -m "docs(roadmap): mark Phase 2c (session persistence) done"
```

---

## 自检（对照 2c 范围）
- ✅ 落盘 `loop` 完整消息历史 H（含最终 answer）→ Task 1-2
- ✅ `--continue`/`--resume` 全量还原整段对话（缺 transcript 回退摘要）→ Task 1-3
- ✅ transcript 存主工作区、worktree 清理后仍在 → Task 2
- ✅ 向后兼容老会话（无 transcript）与老式调用方（无 messages 字段）→ Task 1
- ✅ 只有 loop 消费 prior_messages，其余编排器无副作用 → Task 3
