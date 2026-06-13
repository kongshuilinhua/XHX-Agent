# 默认全屏 + 移除 rich 控制台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `xhx chat`/`xhx tui` 默认启动 Textual 全屏单栏页；把 rich 版独有的 auto-memory 搬到全屏版后，彻底删除 rich `CommandConsole` 及其渲染层与测试。

**Architecture:** 分两阶段。A 先做不可逆删除前的所有准备：main.py 路由改为全屏默认 + 把 auto-memory（跑完 suggest-confirm 写记忆）移植进 `TextualCommandConsoleApp`，复用其已有的 picker + 阻塞确认机制（与 `confirm_terminal_command` 同源）。B 在 A 验证通过后才删 rich 文件与测试。**B 依赖 A，顺序不可换。**

**Tech Stack:** Python 3、Textual（worker 线程 + `call_from_thread` + `present_picker`）、pydantic、pytest。

**背景:** rich 版（`cli/console.py` 的 `CommandConsole`，经 `tui/page.py` 渲染）是和 Textual 全屏版并存的老 REPL。用户要全屏单栏页作为唯一最终效果。rich 版独有、且**无 CLI 替代**的功能只有 auto-memory（`_maybe_suggest_memories`：成功后自动抽候选记忆并逐条确认写入）。`/compact`、`/memory` 有 `xhx compact`/`xhx memory` 顶替，不搬。

---

## File Structure

| 文件 | 动作 |
|---|---|
| `src/xhx_agent/cli/main.py` | 改 `chat`/`tui` 路由为全屏默认；移除 `CommandConsole` import（B 阶段） |
| `src/xhx_agent/tui/textual_app.py` | 移植 auto-memory（A 阶段） |
| `src/xhx_agent/cli/console.py` | **删除**（B 阶段） |
| `src/xhx_agent/tui/page.py` | **删除**（B 阶段） |
| `src/xhx_agent/tui/live.py` | **删除**（B 阶段） |
| `scripts/render_dashboard.py` | **删除**（B 阶段，依赖 page.py） |
| `tests/test_tui_textual.py` | 加 auto-memory 测试（A 阶段） |
| `tests/test_command_console.py`、`test_console_answer.py`、`test_tui_page.py`、`test_tui_live.py`、`test_streaming_render.py` | **删除**（B 阶段，共 26 测试） |

---

## 阶段 A：全屏默认 + 搬 auto-memory

### Task A1: main.py — chat/tui 默认启动全屏

**Files:**
- Modify: `src/xhx_agent/cli/main.py`（`chat` 约 204-215、`tui` 约 218-235）

- [ ] **Step 1: 改 `chat` 命令为全屏**

把 `chat` 命令体替换为（保留 `--profile`）：

```python
@app.command("chat")
def chat(
    profile: Annotated[
        str | None, typer.Option("--profile", help="Model profile name.")
    ] = None,
) -> None:
    workspace = Path.cwd()
    config = load_config(workspace)
    active_profile = profile or config.default_profile
    run_textual_console(workspace=workspace, profile=active_profile)
```

- [ ] **Step 2: 改 `tui` 命令——全屏成为默认，`--fullscreen` 退化为兼容用的 no-op**

```python
@app.command("tui")
def tui(
    fullscreen: Annotated[
        bool, typer.Option("--fullscreen", help="Deprecated: fullscreen is now the default.")
    ] = True,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Model profile name.")
    ] = None,
) -> None:
    workspace = Path.cwd()
    config = load_config(workspace)
    active_profile = profile or config.default_profile
    run_textual_console(workspace=workspace, profile=active_profile)
```

- [ ] **Step 3: 验证（手动驱动，非单测）**

run_textual_console 会进入交互全屏，无法在无 TTY 的 CI 跑。改为静态确认路由不再引用 CommandConsole：

Run: `rg -n "CommandConsole" src/xhx_agent/cli/main.py`
Expected: 仅剩 import 行（B 阶段删）；`chat`/`tui` 函数体内**不再**出现 `CommandConsole(`。

- [ ] **Step 4: 提交**

```bash
git add src/xhx_agent/cli/main.py
git commit -m "feat(cli): launch fullscreen Textual console by default for chat/tui"
```

---

### Task A2: 把 auto-memory 移植进 Textual 全屏 app

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（`__init__`、`run_task` 收尾、新增方法）
- Test: `tests/test_tui_textual.py`

设计：跑完成功后在 **worker 线程**调 `propose_memories`，逐条用 `present_picker` 阻塞确认（复用 `confirm_terminal_command` 的 call_ui + Event 套路），确认就 `write_memory`。全程 best-effort、异常静默——绝不影响任务结果。mock profile 下 `propose_memories` 确定性返回空，所以现有测试不受打扰。

- [ ] **Step 1: 写失败测试**

在 `tests/test_tui_textual.py` 末尾追加（用真实 app 的 worker/确认链路；注入假 candidates 与自动确认）：

```python
def test_textual_auto_memory_suggests_and_writes_on_success(tmp_path, monkeypatch) -> None:
    from xhx_agent.memory.store import MemoryRecord

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=FakeRuntime())

    # 把客户端搭建链路 stub 掉（否则 mock+tmp_path 下 get_profile/build_chat_client 可能抛异常被
    # _maybe_suggest_memories 的 try/except 吞掉，导致拿不到写入而假失败）。
    monkeypatch.setattr("xhx_agent.tui.textual_app.load_config", lambda ws: type("C", (), {"default_profile": "mock"})())
    monkeypatch.setattr("xhx_agent.tui.textual_app.get_profile", lambda ws, name: object())
    monkeypatch.setattr("xhx_agent.tui.textual_app.build_chat_client", lambda profile: object())

    # 注入一个候选记忆，并强制确认返回 True（绕开交互 picker，直接测“成功→提议→写入”链路）。
    cand = MemoryRecord(name="test-fact", description="a fact", mtype="project", body="remember me")
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.propose_memories",
        lambda client, task, digest, existing_names=None: [cand],
    )
    written = {}
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.write_memory",
        lambda workspace, *, name, description, mtype, body: written.update(
            {"name": name, "mtype": mtype, "body": body}
        ),
    )
    monkeypatch.setattr(TextualCommandConsoleApp, "_confirm_memory_blocking", lambda self, c: True)

    app.run_task("do something", announce_user=False, reset_cancel=False)

    assert written.get("name") == "test-fact"
    assert any("Remembered: test-fact" in m for m in app.messages)


def test_textual_auto_memory_skips_when_declined(tmp_path, monkeypatch) -> None:
    from xhx_agent.memory.store import MemoryRecord

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=FakeRuntime())
    monkeypatch.setattr("xhx_agent.tui.textual_app.load_config", lambda ws: type("C", (), {"default_profile": "mock"})())
    monkeypatch.setattr("xhx_agent.tui.textual_app.get_profile", lambda ws, name: object())
    monkeypatch.setattr("xhx_agent.tui.textual_app.build_chat_client", lambda profile: object())
    cand = MemoryRecord(name="test-fact", description="a fact", mtype="project", body="x")
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.propose_memories",
        lambda client, task, digest, existing_names=None: [cand],
    )
    calls = {"wrote": False}
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.write_memory",
        lambda *a, **k: calls.update(wrote=True),
    )
    monkeypatch.setattr(TextualCommandConsoleApp, "_confirm_memory_blocking", lambda self, c: False)

    app.run_task("do something", announce_user=False, reset_cancel=False)

    assert calls["wrote"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tui_textual.py::test_textual_auto_memory_suggests_and_writes_on_success -v`
Expected: FAIL（`propose_memories` 不是 textual_app 的属性 / `_confirm_memory_blocking` 不存在）

- [ ] **Step 3: 实现**

在 `src/xhx_agent/tui/textual_app.py` 顶部 import 区加（模块级，便于测试 monkeypatch）：

```python
from xhx_agent.memory import list_memories, propose_memories, write_memory
from xhx_agent.memory.store import slugify
from xhx_agent.models import build_chat_client
from xhx_agent.runtime.profiles import get_profile
from xhx_agent.runtime.config import load_config
```

在 `TextualCommandConsoleApp.__init__` 末尾加：

```python
        self.auto_memory = True
```

在 `run_task` 末尾（`self.run_pending_steer()` 之前）加一行：

```python
        self._maybe_suggest_memories(result)
```

新增三个方法（建议放在 `apply_run_result` 附近）：

```python
    def _maybe_suggest_memories(self, result: RunResult) -> None:
        """跑完成功后自动抽候选记忆并逐条确认写入。best-effort：任何异常静默跳过，绝不影响任务。

        在 worker 线程执行（propose_memories 是一次模型调用）；确认 UI 经 call_ui 投到 UI 线程。
        mock profile 下 propose_memories 确定性返回空 → 无任何提示。
        """
        if not self.auto_memory or result.status != "success":
            return
        try:
            task = self.state.task or ""
            answer = (getattr(result, "answer", None) or "").strip()
            digest = f"Assistant: {answer}" if answer else f"Run status: {result.status}"
            config = load_config(self.workspace)
            profile = get_profile(self.workspace, self.profile or config.default_profile)
            client = build_chat_client(profile)
            existing = {slugify(r.name) for r in list_memories(self.workspace)}
            candidates = propose_memories(client, task, digest, existing_names=existing)
            for candidate in candidates:
                if self._confirm_memory_blocking(candidate):
                    write_memory(
                        self.workspace,
                        name=candidate.name,
                        description=candidate.description,
                        mtype=candidate.mtype,
                        body=candidate.body,
                    )
                    self.append_message(f"system> Remembered: {candidate.name}")
        except Exception:
            return

    def _confirm_memory_blocking(self, candidate) -> bool:
        """在 worker 线程阻塞，弹一个 记住/跳过 picker，等用户选。非交互/超时一律视为否。"""
        if not self.can_wait_for_interactive_confirmation():
            return False
        done = threading.Event()
        holder = {"resp": False}

        def on_select(selected_id: str | None) -> None:
            holder["resp"] = selected_id == "remember"
            if not done.is_set():
                done.set()

        def show() -> None:
            self._append_message(
                f"system> 记忆候选 [{candidate.mtype}] {candidate.name}: {candidate.description}"
            )
            self.present_picker(
                [("记住（写入长期记忆）", "remember"), ("跳过", "skip")],
                on_select=on_select,
                title="Remember across sessions?",
            )

        self.call_ui(show)
        if not done.wait(self.permission_timeout_seconds):
            holder["resp"] = False
            self.call_ui(lambda: self.resolve_interactive_selection(None))
            done.wait(1.0)
        return bool(holder["resp"])
```

注意：`resolve_interactive_selection(selected_id)` 会调 `on_select(selected_id)` 并拆掉 picker、把焦点还给输入框（已有逻辑），所以 on_select 里只管设结果与 Event。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tui_textual.py -k auto_memory -v`
Expected: PASS（两个用例）

- [ ] **Step 5: 回归**

Run: `python -m pytest tests/test_tui_textual.py -q`
Expected: 全绿（mock 下 propose_memories 返回空，现有用例不受影响；若 FakeRuntime 成功路径触发了真实 propose_memories 导致异常，确认 `_maybe_suggest_memories` 的 try/except 把它吞掉了）。

- [ ] **Step 6: 提交**

```bash
git add src/xhx_agent/tui/textual_app.py tests/test_tui_textual.py
git commit -m "feat(tui): port auto-memory suggest-confirm into fullscreen console"
```

---

### Task A3: 真机验收 A 阶段（交给计划发起者/Claude 验收）

- [ ] 真实联通（DeepSeek + UTF-8）启动 `xhx tui`，跑一个会产出可记忆事实的任务，确认：① 默认就是全屏单栏页（无需 --fullscreen）；② 跑完成功后弹出「记忆候选 + 记住/跳过」picker，选「记住」后 `memory/` 真的写入新文件且主区出现 `Remembered: <name>`。

---

## 阶段 B：删除 rich 控制台（A 验证通过后才做）

### Task B1: 删 rich 文件 + 测试 + 解除 main.py 引用

**Files:**
- Delete: `src/xhx_agent/cli/console.py`、`src/xhx_agent/tui/page.py`、`src/xhx_agent/tui/live.py`、`scripts/render_dashboard.py`
- Delete: `tests/test_command_console.py`、`tests/test_console_answer.py`、`tests/test_tui_page.py`、`tests/test_tui_live.py`、`tests/test_streaming_render.py`
- Modify: `src/xhx_agent/cli/main.py`（移除 `from xhx_agent.cli.console import CommandConsole`）

- [ ] **Step 1: 先全量定位所有引用（避免漏网导致 import 崩）**

Run: `rg -n "cli\.console|CommandConsole|tui\.page|render_console_page|tui\.live|LiveDashboard" src scripts tests`
记录除「待删文件自身」「main.py 的那一行 import」「textual_app.py 中类名 `TextualCommandConsoleApp`（误命中，保留）」之外的所有命中——若有 src 下其它真实引用，必须在本步一并处理（大概率没有，A 阶段已把 main.py 路由摘干净）。

- [ ] **Step 2: 移除 main.py 的 CommandConsole import**

删除 `src/xhx_agent/cli/main.py` 中这一行：

```python
from xhx_agent.cli.console import CommandConsole
```

- [ ] **Step 3: 删除 rich 源文件与测试**

```bash
git rm src/xhx_agent/cli/console.py src/xhx_agent/tui/page.py src/xhx_agent/tui/live.py scripts/render_dashboard.py
git rm tests/test_command_console.py tests/test_console_answer.py tests/test_tui_page.py tests/test_tui_live.py tests/test_streaming_render.py
```

- [ ] **Step 4: 确认 import 与全量测试**

Run: `python -c "import xhx_agent.cli.main; print('main imports OK')"`
Expected: 打印 OK（无 ImportError）

Run: `python -m pytest tests/ -q`
Expected: 全绿。若有红，多半是 Step 1 漏掉的引用——回 Step 1 处理。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(cli): remove legacy rich CommandConsole; fullscreen Textual is the only console"
```

---

## Self-Review 记录

- **覆盖**：全屏默认→A1；auto-memory 不丢失→A2（移植）+A3（验收）；彻底删 rich→B1（源文件+测试+main.py 引用）。`/compact`、`/memory` 有 `xhx compact`/`xhx memory` 顶替，确认不搬。
- **顺序依赖**：B 必须在 A 验证后；A1 摘掉 main.py 对 CommandConsole 的运行期使用，B1 才只剩删一行 import。
- **占位扫描**：无 TBD；移植代码与删除清单均为可直接执行的真实内容。
- **类型/命名一致**：`propose_memories(client, task, digest, existing_names=)`、`write_memory(workspace, name=, description=, mtype=, body=)`、`MemoryRecord(.name/.description/.mtype/.body)`、`_maybe_suggest_memories`/`_confirm_memory_blocking` 在 A2 内自洽，与 rich 版既有调用签名一致。
- **风险**：(1) 移除 rich 后无非 TTY fallback——Textual 需真实终端；可接受（用户只用全屏）。(2) auto-memory 在 worker 线程跑模型调用 + UI 线程弹确认，已复用 `confirm_terminal_command` 的 call_ui+Event 成熟套路，超时/非交互安全返回否。
</content>
