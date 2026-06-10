# `loop`（ReAct）范式 Phase 1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `loop`（ReAct tool-use 统一循环）范式——支持正常对话，并用原生 tool-calling 调用 read_file/search/apply_patch 真实干活，全程复用现有安全内核/worktree/证据链。

**Architecture:** 新建 `orchestrators/loop.py`，实现既有 `Orchestrator` 协议；模型层新增 `chat(messages, tools)`（原生 `tool_calls`）；工具层引入声明式 `ToolDefinition` 并导出 OpenAI function schema。循环：模型回纯文本=对话回答即结束；回 `tool_calls`=经 `kernel.execute_tool` 执行、结果作为 `role:tool` 消息追加、再循环。开工前先把旧 `loop`（plan-execute）改名为 `plan` 腾出名字。

**Tech Stack:** Python 3.13、pydantic、httpx、pytest、uv。LLM：OpenAI 兼容（DeepSeek，已连通）+ 离线 `mock`。

**上位文档：** [设计 spec](../specs/2026-06-10-agent-tool-calling-conversation-design.md) · [ROADMAP](../../../ROADMAP.md)

**运行约定（Windows）：** 所有命令前置 `PYTHONUTF8=1`；测试用 `uv run pytest`。

---

## 文件结构（本 Phase 触及）

- `src/xhx_agent/orchestrators/plan.py` —— 由 `loop.py` 改名而来（旧 plan-execute），类 `PlanOrchestrator`
- `src/xhx_agent/orchestrators/loop.py` —— **新建**，类 `LoopOrchestrator`（ReAct 循环）
- `src/xhx_agent/orchestrators/registry.py` —— 模式注册表：加 `plan`、`loop` 指向新实现
- `src/xhx_agent/models/types.py` —— 新增 `ToolCall` / `ChatResult`
- `src/xhx_agent/models/openai_compatible.py` —— 新增 `chat(messages, tools)`
- `src/xhx_agent/models/mock.py` —— 新增 `chat(messages, tools)`（确定性模拟）
- `src/xhx_agent/models/__init__.py` —— 新增 `build_chat_client(profile)` 工厂（便于测试注入）
- `src/xhx_agent/tools/registry.py` —— 新增 `ToolDefinition` + `tool_schemas()`
- `src/xhx_agent/runtime/app.py` —— `RunResult` 加 `answer` 字段；`_run_loop` 辅助（或在 orchestrator 内）
- `src/xhx_agent/cli/console.py` —— REPL 渲染 `answer`
- 测试：`tests/test_loop_orchestrator.py`、`tests/test_chat_client.py`、`tests/test_tool_schemas.py`、更新现有引用 `loop` 的测试

---

## Task 1: 实现前验证 DeepSeek 的 tool_calls 返回格式（spike · 手动）

**目的：** 在写客户端前确认真实返回字段（`tool_calls[].id` / `function.name` / `function.arguments` 是否 JSON 字符串），避免照错格式写。

**Files:** 无（一次性脚本，不入库）

- [ ] **Step 1: 发一个最小 tools 请求并打印原始响应**

Run（用你的 key，临时注入）:
```bash
DEEPSEEK_API_KEY="<your-key>" PYTHONUTF8=1 uv run python -c "
import os, json, httpx
r = httpx.post('https://api.deepseek.com/v1/chat/completions',
  headers={'Authorization':'Bearer '+os.environ['DEEPSEEK_API_KEY']},
  json={'model':'deepseek-chat','messages':[{'role':'user','content':\"What's the weather in Beijing? Use the tool.\"}],
        'tools':[{'type':'function','function':{'name':'get_weather','description':'get weather','parameters':{'type':'object','properties':{'city':{'type':'string'}},'required':['city']}}}],
        'tool_choice':'auto'}, timeout=60).json()
print(json.dumps(r['choices'][0]['message'], ensure_ascii=False, indent=2))
"
```
Expected: 看到 `message.tool_calls[0].id`、`.function.name == "get_weather"`、`.function.arguments` 是**JSON 字符串**（如 `"{\"city\": \"Beijing\"}"`）。

- [ ] **Step 2: 记录结论**

把观察到的字段结构记到 spec §10 开放问题 #1 下方（一句话：arguments 为 JSON 字符串需 `json.loads`）。若与假设不符，先停下同步。

---

## Task 2: `RunResult` 增加 `answer` 字段

**Files:**
- Modify: `src/xhx_agent/runtime/app.py:67-82`（`RunResult`）
- Test: `tests/test_run_result.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_run_result.py
from xhx_agent.runtime.app import RunResult

def _base(**kw):
    return RunResult(run_id="r1", status="success", changed_files=[], commands=[],
                     verification="skipped", summary_path="p", risk_summary=[], **kw)

def test_answer_defaults_none():
    assert _base().answer is None

def test_answer_accepts_text():
    assert _base(answer="hello").answer == "hello"
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_run_result.py -q`
Expected: FAIL（`answer` 不是合法字段 / AttributeError）

- [ ] **Step 3: 加字段**

在 `RunResult`（app.py:82 `mode: str = ""` 之后）加：
```python
    answer: str | None = None
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONUTF8=1 uv run pytest tests/test_run_result.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_run_result.py src/xhx_agent/runtime/app.py
git commit -m "feat(runtime): add RunResult.answer for conversational loop output"
```

---

## Task 3: Step 0 —— 把旧 `loop` 改名为 `plan`（只改名、行为不变）

**Files:**
- Rename: `src/xhx_agent/orchestrators/loop.py` → `src/xhx_agent/orchestrators/plan.py`
- Modify: `src/xhx_agent/orchestrators/registry.py`
- Test: `tests/test_orchestrator_registry.py`

- [ ] **Step 1: 写失败测试（plan 可选中、loop 暂作别名仍指向 plan 行为）**

```python
# tests/test_orchestrator_registry.py
from xhx_agent.orchestrators.registry import select_orchestrator

def test_plan_mode_selects_plan_orchestrator():
    assert select_orchestrator("plan").name == "plan"

def test_loop_alias_still_plan_for_now():
    # 改名阶段：loop 暂作 plan 的别名，行为不变（新 loop 在后续任务接管）
    assert select_orchestrator("loop").name == "plan"
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_orchestrator_registry.py -q`
Expected: FAIL（`plan` 未注册）

- [ ] **Step 3: 改名文件与类**

`git mv src/xhx_agent/orchestrators/loop.py src/xhx_agent/orchestrators/plan.py`，并把内容改为：
```python
# src/xhx_agent/orchestrators/plan.py
from __future__ import annotations
from typing import TYPE_CHECKING
from xhx_agent.orchestrators.base import OrchestratorContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class PlanOrchestrator:
    """plan 范式：自主 plan-execute 循环（原 loop 改名而来，行为不变）。

    复用 RuntimeApp._run_linear，开启 autonomous：模型持续迭代读→改→验证多轮，
    直到自报完成或触达 config.max_loop_turns。tool-calling 迁移在 Phase 3。
    """

    name = "plan"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        ctx.autonomous = True
        return ctx.app._run_linear(ctx)
```

- [ ] **Step 4: 更新 registry（加 plan，loop 暂作别名指向 PlanOrchestrator）**

`registry.py`：
- import 改 `from xhx_agent.orchestrators.plan import PlanOrchestrator`
- `_ORCHESTRATORS` 改为：
```python
_ORCHESTRATORS: dict[str, type] = {
    "plan": PlanOrchestrator,   # 自主 plan-execute（原 loop 改名）
    "loop": PlanOrchestrator,   # 临时别名：新 ReAct loop 在 Task 7 接管
    "linear": LinearOrchestrator,
    "dag": DagOrchestrator,
    "graph": GraphOrchestrator,
}
```
- `DEFAULT_MODE` 保持 `"loop"`（开发期等价旧默认；Task 8 再决定是否切到 `plan`/新 `loop`）。
- `execution_mode_to_key` 不变（auto-classify 仍落 linear/dag）。

- [ ] **Step 5: 更新现有对旧 loop 的引用**

Run 搜索: `PYTHONUTF8=1 uv run python -c "import subprocess; print('grep manually')"` —— 用 ripgrep 找引用：
```bash
rg -n "LoopOrchestrator|orchestrators.loop|orchestrators\\.loop" src tests
```
把命中处（如 `__init__` 导出、测试）从 `LoopOrchestrator`/`orchestrators.loop` 改为 `PlanOrchestrator`/`orchestrators.plan`。`--mode loop` 的行为测试**无需改**（别名仍指向 plan 行为）。

- [ ] **Step 6: 运行全套测试确认无回归**

Run: `PYTHONUTF8=1 uv run pytest -q`
Expected: PASS（行为不变，仅改名）

- [ ] **Step 7: 提交**

```bash
git add -A
git commit -m "refactor(orchestrators): rename loop->plan (name only); loop kept as alias"
```

---

## Task 4: 声明式工具接口（`ToolDefinition` + `tool_schemas()`）

**Files:**
- Modify: `src/xhx_agent/tools/registry.py`
- Test: `tests/test_tool_schemas.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tool_schemas.py
from xhx_agent.tools.registry import default_tool_registry

def test_tool_schemas_lists_three_tools():
    schemas = default_tool_registry().tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"search", "read_file", "apply_patch"}

def test_schema_shape_openai_function():
    schemas = default_tool_registry().tool_schemas()
    s = next(s for s in schemas if s["function"]["name"] == "read_file")
    assert s["type"] == "function"
    assert s["function"]["parameters"]["required"] == ["path"]
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_tool_schemas.py -q`
Expected: FAIL（`tool_schemas` 不存在）

- [ ] **Step 3: 在 registry.py 增加 ToolDefinition 与 schema 导出**

在 `ToolRegistry` 增加（不破坏现有 `register`/`execute`/`validate_plan`）：
```python
# 顶部新增
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema
    read_only: bool = False
    destructive: bool = False

# 模块级：三件套的声明
TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "search": ToolDefinition(
        name="search", description="在仓库内按文本搜索，返回匹配的文件/行。只读。",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索文本"},
            "glob": {"type": "string", "description": "可选文件名 glob，如 *.py"},
            "max_results": {"type": "integer", "default": 50}},
            "required": ["query"]},
        read_only=True),
    "read_file": ToolDefinition(
        name="read_file", description="按行读取仓库内文件内容。只读。",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "相对路径"},
            "start_line": {"type": "integer", "default": 1},
            "max_lines": {"type": "integer", "default": 200}},
            "required": ["path"]},
        read_only=True),
    "apply_patch": ToolDefinition(
        name="apply_patch", description="用 *** Begin Patch/*** End Patch 格式对文件做增量修改。会改文件。",
        parameters={"type": "object", "properties": {
            "patch": {"type": "string", "description": "完整 patch 文本"}},
            "required": ["patch"]},
        destructive=True),
}
```
在 `ToolRegistry` 加方法：
```python
    def tool_schemas(self) -> list[dict[str, Any]]:
        """导出已注册工具的 OpenAI function 格式 schema（喂给模型的 tools 参数）。"""
        return [
            {"type": "function", "function": {
                "name": d.name, "description": d.description, "parameters": d.parameters}}
            for name, d in TOOL_DEFINITIONS.items() if name in self._tools
        ]
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONUTF8=1 uv run pytest tests/test_tool_schemas.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_tool_schemas.py src/xhx_agent/tools/registry.py
git commit -m "feat(tools): declarative ToolDefinition + tool_schemas() for tool-calling"
```

---

## Task 5: tool-calling 客户端 `chat(messages, tools)`

**Files:**
- Modify: `src/xhx_agent/models/types.py`（加 `ToolCall` / `ChatResult`）
- Modify: `src/xhx_agent/models/openai_compatible.py`（加 `chat`）
- Test: `tests/test_chat_client.py`

- [ ] **Step 1: 写失败测试（用假 http_client 注入响应）**

```python
# tests/test_chat_client.py
import json
import httpx
from xhx_agent.models.openai_compatible import OpenAICompatibleClient

class _FakeResp:
    status_code = 200
    def __init__(self, payload): self._p = payload
    def json(self): return self._p

class _FakeHTTP:
    def __init__(self, payload): self._p = payload; self.last = None
    def post(self, url, headers=None, json=None): self.last = json; return _FakeResp(self._p)

def _client(payload, monkeypatch):
    monkeypatch.setenv("XHX_TEST_KEY", "sk-test")
    return OpenAICompatibleClient(base_url="http://x/v1", api_key_env="XHX_TEST_KEY",
                                  model="m", http_client=_FakeHTTP(payload))

def test_chat_returns_text(monkeypatch):
    payload = {"choices": [{"message": {"content": "hi there", "tool_calls": None}}]}
    res = _client(payload, monkeypatch).chat([{"role": "user", "content": "hi"}], tools=[])
    assert res.content == "hi there"
    assert res.tool_calls == []

def test_chat_parses_tool_calls(monkeypatch):
    payload = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}}]}}]}
    res = _client(payload, monkeypatch).chat([{"role": "user", "content": "read a.py"}], tools=[])
    assert res.content is None
    assert len(res.tool_calls) == 1
    tc = res.tool_calls[0]
    assert (tc.id, tc.name, tc.arguments) == ("call_1", "read_file", {"path": "a.py"})
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_chat_client.py -q`
Expected: FAIL（`chat` / `ChatResult` 不存在）

- [ ] **Step 3: 在 types.py 加 ToolCall / ChatResult**

```python
# models/types.py 追加
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

class ChatResult(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
```

- [ ] **Step 4: 在 openai_compatible.py 加 chat（非流式，Phase 1）**

```python
# openai_compatible.py 顶部 import 补 ChatResult, ToolCall
from xhx_agent.models.types import ModelClientError, ModelPlan, ChatResult, ToolCall

# 类内新增方法
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatResult:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ModelClientError(code="missing_api_key",
                message=f"Missing API key environment variable: {self.api_key_env}",
                details={"api_key_env": self.api_key_env})
        payload: dict[str, Any] = {
            "model": self.model, "temperature": self.temperature, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            response = self.http_client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload)
        except httpx.HTTPError as exc:
            raise ModelClientError(code="network_error", message=f"Chat request failed: {exc}",
                details={"error": str(exc)}) from exc
        if response.status_code >= 400:
            raise ModelClientError(code="http_error",
                message=f"Chat request returned HTTP {response.status_code}.",
                details={"status_code": response.status_code, "body": response.text[:1000]})
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelClientError(code="invalid_response",
                message="Chat response missing choices[0].message.",
                details={"body": response.text[:1000]}) from exc
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", {})
            args = raw_args
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError as exc:
                    raise ModelClientError(code="invalid_tool_arguments",
                        message=f"tool_call arguments not valid JSON: {raw_args[:200]}",
                        details={"arguments": raw_args[:1000]}) from exc
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args or {}))
        content = message.get("content")
        return ChatResult(content=content if isinstance(content, str) else None, tool_calls=tool_calls)
```

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONUTF8=1 uv run pytest tests/test_chat_client.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add tests/test_chat_client.py src/xhx_agent/models/types.py src/xhx_agent/models/openai_compatible.py
git commit -m "feat(models): tool-calling chat() returning ChatResult (text or tool_calls)"
```

---

## Task 6: Mock 客户端 `chat()` + `build_chat_client` 工厂

**Files:**
- Modify: `src/xhx_agent/models/mock.py`（加 `chat`）
- Modify: `src/xhx_agent/models/__init__.py`（加 `build_chat_client`）
- Test: `tests/test_chat_client.py`（追加 mock 用例）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_chat_client.py 追加
from xhx_agent.models import build_chat_client
from xhx_agent.runtime.profiles import ModelProfile

def test_mock_chat_question_returns_text():
    client = build_chat_client(ModelProfile(name="mock", provider="mock", base_url="", api_key_env="", model="mock"))
    res = client.chat([{"role": "user", "content": "你是谁"}], tools=[])
    assert res.content and not res.tool_calls

def test_mock_chat_edit_then_done():
    client = build_chat_client(ModelProfile(name="mock", provider="mock", base_url="", api_key_env="", model="mock"))
    # turn 1: 无 tool 结果 + 编辑类任务 → 给一个 read_file tool_call
    msgs = [{"role": "user", "content": "fix the bug in a.py"}]
    r1 = client.chat(msgs, tools=[])
    assert r1.tool_calls and r1.tool_calls[0].name == "read_file"
    # turn 2: 出现过 tool 结果 → 直接给最终文本
    msgs += [{"role": "tool", "tool_call_id": r1.tool_calls[0].id, "content": "file content"}]
    r2 = client.chat(msgs, tools=[])
    assert r2.content and not r2.tool_calls
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_chat_client.py -q`
Expected: FAIL（`build_chat_client` / mock `chat` 不存在）

- [ ] **Step 3: 在 mock.py 加 chat（确定性）**

```python
# mock.py 类内新增（import: from xhx_agent.models.types import ChatResult, ToolCall, MockPlan, ToolStep）
    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        edit_words = ("fix", "修", "改", "加", "patch", "refactor", "重构")
        is_edit = any(w in str(last_user).lower() for w in edit_words)
        if is_edit and not has_tool_result:
            return ChatResult(content=None, tool_calls=[
                ToolCall(id="mock_call_1", name="read_file", arguments={"path": "README.md"})])
        return ChatResult(content="Mock loop reply: 任务已处理（确定性 mock）。", tool_calls=[])
```

- [ ] **Step 4: 在 models/__init__.py 加工厂**

```python
# models/__init__.py
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from xhx_agent.runtime.profiles import ModelProfile

def build_chat_client(profile: "ModelProfile"):
    """按 profile.provider 构造支持 chat(messages, tools) 的客户端。"""
    if profile.provider == "mock":
        from xhx_agent.models.mock import MockModelClient
        return MockModelClient()
    if profile.provider == "openai-compatible":
        from xhx_agent.models.openai_compatible import OpenAICompatibleClient
        return OpenAICompatibleClient(base_url=profile.base_url, api_key_env=profile.api_key_env,
                                      model=profile.model, temperature=profile.temperature)
    from xhx_agent.models.types import ModelClientError
    raise ModelClientError(code="unsupported_provider",
        message=f"Unsupported model provider: {profile.provider}", details={"provider": profile.provider})
```

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONUTF8=1 uv run pytest tests/test_chat_client.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add tests/test_chat_client.py src/xhx_agent/models/mock.py src/xhx_agent/models/__init__.py
git commit -m "feat(models): deterministic mock chat() + build_chat_client factory"
```

---

## Task 7: 新建 `loop` orchestrator（ReAct 循环）

**Files:**
- Create: `src/xhx_agent/orchestrators/loop.py`
- Modify: `src/xhx_agent/orchestrators/registry.py`（`loop` → 新 `LoopOrchestrator`）
- Test: `tests/test_loop_orchestrator.py`

- [ ] **Step 1: 写失败测试（mock profile 驱动，断言对话与干活两条路）**

```python
# tests/test_loop_orchestrator.py
from pathlib import Path
from xhx_agent.runtime.app import RuntimeApp

def _run(tmp_path: Path, task: str):
    # init 工作区（建 .xhx、索引）
    app = RuntimeApp(tmp_path)
    app.init_workspace()  # 若方法名不同，依现有 init 流程调整
    return app.run_task(task, profile_name="mock", mode="loop")

def test_loop_conversation_returns_answer(tmp_path):
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    res = _run(tmp_path, "你是谁，介绍一下")
    assert res.status == "success"
    assert res.mode == "loop"
    assert res.answer and "mock" in res.answer.lower()

def test_loop_edit_task_runs_tool_then_answers(tmp_path):
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    res = _run(tmp_path, "fix the bug in README.md")
    assert res.status == "success"
    assert res.mode == "loop"
    # mock：turn1 read_file → turn2 文本结束；answer 非空
    assert res.answer
```

> 注：若 `RuntimeApp` 的初始化/`run_task` 签名与此处不符，以现有 `cli/main.py` 调用方式为准（`run_task(task, profile_name=..., mode=...)`），不要改其它范式行为。

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py -q`
Expected: FAIL（`loop` 仍指向 PlanOrchestrator，mode 不是 "loop" 或无 answer）

- [ ] **Step 3: 写 loop.py（ReAct 循环）**

```python
# src/xhx_agent/orchestrators/loop.py
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from xhx_agent.models import build_chat_client
from xhx_agent.models.types import ModelClientError, ToolStep
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.runtime.events import emit_event

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult

LOOP_SYSTEM_PROMPT = (
    "You are xhx-agent, a coding agent operating inside a local repository.\n"
    "Answer the user's questions directly in natural language. Only call tools when code work is needed.\n"
    "Use relative paths only. All writes go through apply_patch. If evidence is insufficient, "
    "read_file/search first before patching. Do not assume unread files."
)
_MAX_TOOL_RESULT_CHARS = 8000


class LoopOrchestrator:
    """loop 范式：ReAct tool-use 统一循环（Claude Code 式）。

    模型回纯文本=对话回答即结束；回 tool_calls=经 kernel 执行、结果作为 role:tool 消息追加、再循环。
    """

    name = "loop"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.runtime.app import RunResult

        client = build_chat_client(ctx.profile)
        schemas = ctx.tool_context_registry_schemas()  # 见 Step 4：辅助拿 tool_schemas
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": LOOP_SYSTEM_PROMPT + "\n\n" + ctx.scan.project_map_text()},
            {"role": "user", "content": ctx.task},
        ]
        changed_files: list[str] = []
        risks: list[str] = []
        max_turns = ctx.app.config.max_loop_turns
        answer: str | None = None
        status = "success"

        for turn in range(1, max_turns + 1):
            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"; risks.append("cancelled before model call"); break
            try:
                result = client.chat(messages, schemas)
            except ModelClientError as exc:
                ctx.evidence.write_trace("model_error", {"turn": turn, **exc.to_trace_payload()})
                emit_event(ctx.event_callback, "model_error", exc.message, turn=turn, code=exc.code)
                status = "failed"; risks.append(exc.message); break

            if not result.tool_calls:
                answer = result.content or ""
                emit_event(ctx.event_callback, "model_plan", f"loop answer [turn {turn}]", turn=turn, status="done")
                break

            messages.append({"role": "assistant", "content": result.content or "", "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in result.tool_calls]})

            for tc in result.tool_calls:
                step = ToolStep(tool=tc.name, arguments=tc.arguments)
                emit_event(ctx.event_callback, "tool_start", f"tool: {tc.name}", turn=turn, tool=tc.name)
                exec_result, _trace, policy = ctx.kernel.execute_tool(ctx.tool_context, step, turn, ctx.event_callback)
                if exec_result is None:
                    content = f"Tool denied/blocked: {policy.reason}"
                else:
                    content = _render_tool_content(exec_result)
                    if exec_result.changed_files:
                        changed_files.extend(exec_result.changed_files)
                emit_event(ctx.event_callback, "tool_result", "tool done", turn=turn, tool=tc.name)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
        else:
            status = "failed"; risks.append(f"loop 在 {max_turns} 轮内未结束")

        return ctx.app._finalize_loop_result(
            ctx, status=status, changed_files=changed_files, risks=risks, answer=answer)


def _render_tool_content(result: Any) -> str:
    if result.status != "success":
        return f"[{result.tool} failed] {result.error or result.summary}"
    payload = result.trace_payload or {}
    for key in ("content", "results"):
        if key in payload:
            return f"{result.summary}\n{json.dumps(payload[key], ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]}"
    return result.summary
```

> 上面用到两个**辅助**，在 Step 4 落地：`ctx.scan.project_map_text()`（精简项目地图文本）、`ctx.tool_context_registry_schemas()`（拿 tool_schemas）、`ctx.app._finalize_loop_result(...)`（复用现有 evidence/summary 落盘 + 组装 RunResult）。若现有 `ProjectScan`/`RuntimeApp` 已有等价方法，直接用现成的，不要重复造。

- [ ] **Step 4: 落地三个辅助（复用现有，缺则补薄封装）**

1) `ProjectScan.project_map_text()`：若已有渲染项目地图的方法（init 写 `XHX.md` 时用过）直接复用；否则在 `runtime` 里加一个把 scan 摘要成几行文本的纯函数，`loop.py` 改为调它。
2) tool_schemas 获取：`OrchestratorContext` 已持有 `kernel`，而 `kernel.tool_registry` 是 `ToolRegistry`。把 `ctx.tool_context_registry_schemas()` 替换为 `ctx.kernel.tool_registry.tool_schemas()`（无需新增方法）。
3) `_finalize_loop_result`：参照 `_run_linear` 结尾如何写 `run_end` trace、生成 summary、组装 `RunResult`，抽一个最小辅助：
```python
# app.py 内
def _finalize_loop_result(self, ctx, *, status, changed_files, risks, answer):
    from xhx_agent.evidence.report import write_report  # 若现成 summary 写法不同，沿用 _run_linear 的
    ctx.evidence.write_trace("run_end", {"status": status})
    summary = self._write_summary(ctx, status=status, changed_files=changed_files)  # 复用现有 summary 写法
    return RunResult(
        run_id=ctx.run_id, status=status, changed_files=sorted(set(changed_files)),
        commands=[], verification="skipped_no_verification",
        summary_path=str(summary.relative_to(ctx.original_workspace)),
        risk_summary=risks, mode=ctx.mode or "loop", answer=answer)
```
> 实现者按 `_run_linear` 现有的 summary/落盘写法对齐字段，**不要新造 summary 机制**。

- [ ] **Step 5: registry 把 `loop` 指向新 LoopOrchestrator**

`registry.py`：
```python
from xhx_agent.orchestrators.loop import LoopOrchestrator
# _ORCHESTRATORS 中 "loop" 改为：
    "loop": LoopOrchestrator,   # 新 ReAct loop（接管别名）
```
同时把 Task 3 里 `tests/test_orchestrator_registry.py::test_loop_alias_still_plan_for_now` 改为新断言：
```python
def test_loop_mode_selects_loop_orchestrator():
    assert select_orchestrator("loop").name == "loop"
```

- [ ] **Step 6: 运行确认通过**

Run: `PYTHONUTF8=1 uv run pytest tests/test_loop_orchestrator.py tests/test_orchestrator_registry.py -q`
Expected: PASS

- [ ] **Step 7: 全套回归**

Run: `PYTHONUTF8=1 uv run pytest -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add -A
git commit -m "feat(orchestrators): add loop (ReAct tool-calling) paradigm"
```

---

## Task 8: 接 `--mode loop` / `/mode loop` + REPL 渲染 answer

**Files:**
- Modify: `src/xhx_agent/cli/console.py`（mode 校验集合加 `plan`/`loop`；渲染 answer）
- Modify: `src/xhx_agent/tui/textual_app.py`（mode 校验集合，同步）
- Test: `tests/test_console_answer.py`

- [ ] **Step 1: 写失败测试（REPL 渲染 answer）**

```python
# tests/test_console_answer.py
from xhx_agent.cli.console import CommandConsole
from xhx_agent.runtime.app import RunResult

def test_print_run_result_shows_answer(tmp_path, capsys):
    console = CommandConsole(workspace=tmp_path)
    res = RunResult(run_id="r1", status="success", changed_files=[], commands=[],
                    verification="skipped", summary_path="p", risk_summary=[], mode="loop",
                    answer="这是 loop 的回答")
    console.print_run_result(res)
    out = capsys.readouterr().out
    assert "这是 loop 的回答" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONUTF8=1 uv run pytest tests/test_console_answer.py -q`
Expected: FAIL（answer 未渲染）

- [ ] **Step 3: console.py 渲染 answer + 加模式名**

1) `orchestrator_mode` 的合法集合 `{"loop","graph","linear","dag"}` 改为 `{"plan","loop","graph","linear","dag"}`（console.py 与 textual_app.py 各一处）。
2) `print_run_result` 开头加：
```python
        if result.answer:
            from rich.panel import Panel
            self.console.print(Panel(result.answer, title="Answer", border_style="green"))
```

- [ ] **Step 4: 运行确认通过**

Run: `PYTHONUTF8=1 uv run pytest tests/test_console_answer.py -q`
Expected: PASS

- [ ] **Step 5: 全套回归 + lint**

Run: `PYTHONUTF8=1 uv run pytest -q && uv run ruff check .`
Expected: PASS / no errors

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(cli): select --mode loop/plan and render loop answer in REPL"
```

---

## Task 9: 真实联调 + 文档（手动验证）

**Files:** 无代码改动（除非联调暴露 bug）

- [ ] **Step 1: 真实 DeepSeek 跑一次对话**

Run:
```bash
DEEPSEEK_API_KEY="<key>" PYTHONUTF8=1 uv run xhx run "用一句话说明 orchestrator 架构" --profile default --mode loop
```
Expected: 看到 Answer 面板有真实中文回答（非空、非 mock 文案）。

- [ ] **Step 2: 真实跑一次小编辑（可还原）**

Run:
```bash
DEEPSEEK_API_KEY="<key>" PYTHONUTF8=1 uv run xhx run "在 README.md 末尾追加一行注释 <!-- loop tested -->" --profile default --mode loop
git --no-pager diff README.md   # 看改动
git checkout README.md          # 还原
```
Expected: loop 经 read_file→apply_patch 真实改了 README，diff 可见。

- [ ] **Step 3: 更新文档状态**

- spec 顶部状态：`草案` → `Phase 1 已实现`。
- ROADMAP Phase 1 行尾标注 `（已实现）`。
- 提交：
```bash
git add docs/ ROADMAP.md
git commit -m "docs: mark Phase 1 loop paradigm implemented"
```

---

## 自检（写完计划后对照 spec）

- ✅ 对话（§2.2 目标2）→ Task 7 test_loop_conversation_returns_answer
- ✅ tool-calling 干活（目标3）→ Task 7 test_loop_edit + Task 9 真实编辑
- ✅ 复用 kernel/worktree/evidence（目标4）→ Task 7 Step 3 用 `kernel.execute_tool`
- ✅ 离线 mock（目标5）→ Task 6
- ✅ 声明式工具接口（目标6）→ Task 4
- ✅ 命名撞车顺序（§2.1）→ Task 3（改名）+ Task 7 Step 5（loop 接管）
- ✅ RunResult.answer（§3 部件6）→ Task 2
- ✅ DeepSeek 格式验证（§10 #1）→ Task 1
- ✅ 模式校验集合更新（§10 #2）→ Task 3 Step 4 + Task 8 Step 3
- ⚠️ 默认模式切换时机（§10 #4）→ 暂留 DEFAULT_MODE="loop"（=新范式，Task 7 后），如需保守可在 Task 8 改回 "plan"；执行时确认。
```
