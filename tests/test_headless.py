"""headless 驱动测试：用脚本化的假模型客户端驱动统一 Agent 循环跑到完成。

同时作为新栈（agent_runner.Agent）的第一个端到端测试夹具。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from xhx_agent.client import LLMClient
from xhx_agent.conversation import ConversationManager
from xhx_agent.runtime.headless import build_headless_agent, run_headless_task
from xhx_agent.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete


class FakeLLMClient(LLMClient):
    """按脚本逐轮产出 StreamEvent。每次 stream() 调用消费下一个 turn，越界则复用最后一个。"""

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        self._turns = list(turns)
        self.calls = 0

    async def stream(
        self,
        conversation: Any,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for event in self._turns[idx]:
            yield event


def test_headless_executes_tool_and_returns_summary(tmp_path: Path, monkeypatch: Any) -> None:
    # 文件类工具按进程 cwd 解析相对路径，真实 `xhx run` 在项目根运行（cwd==workspace）。
    monkeypatch.chdir(tmp_path)
    client = FakeLLMClient(
        [
            [
                ToolCallComplete(
                    tool_id="t1",
                    tool_name="WriteFile",
                    arguments={"file_path": "note.txt", "content": "hello"},
                ),
                StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5),
            ],
            [
                TextDelta("done"),
                StreamEnd(stop_reason="end_turn", input_tokens=3, output_tokens=2),
            ],
        ]
    )

    result = run_headless_task(tmp_path, "write a note", assume_yes=True, client=client)

    assert result.status == "completed"
    assert result.summary == "done"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"
    assert result.input_tokens == 13
    assert result.output_tokens == 7
    assert client.calls == 2  # 一轮工具 + 一轮收尾


def test_headless_with_instructions_injects_memory(tmp_path: Path, monkeypatch: Any) -> None:
    # 项目有 XHX.md（instructions）时会走「注入长期记忆」分支，曾因 MemoryManager 缺 load() 崩溃。
    monkeypatch.chdir(tmp_path)
    (tmp_path / "XHX.md").write_text("# 项目说明\n测试项目。\n", encoding="utf-8")
    client = FakeLLMClient(
        [
            [
                TextDelta("你好"),
                StreamEnd(stop_reason="end_turn", input_tokens=2, output_tokens=2),
            ],
        ]
    )

    result = run_headless_task(tmp_path, "打个招呼", client=client)

    assert result.status == "completed"
    assert result.summary == "你好"


def test_headless_reports_mcp_connect_failure(tmp_path: Path, monkeypatch: Any) -> None:
    """MCP server 连不上时任务照常完成，但失败必须可见：事件流 mcp_error + trace 落盘。

    回归保护：此前 connect_all 的 on_error 传 None 且异常整体吞掉，
    配了 server 却没工具时完全无从排查。
    """
    import json

    monkeypatch.chdir(tmp_path)
    xhx = tmp_path / ".xhx"
    xhx.mkdir()
    (xhx / "mcp.json").write_text(
        json.dumps({"servers": [{"name": "ghost", "command": "this_command_does_not_exist_xhx_test"}]}),
        encoding="utf-8",
    )
    client = FakeLLMClient(
        [
            [
                TextDelta("ok"),
                StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1),
            ],
        ]
    )
    events: list[dict[str, Any]] = []

    result = run_headless_task(tmp_path, "say ok", client=client, event_callback=events.append)

    assert result.status == "completed"
    mcp_errors = [e for e in events if e.get("type") == "mcp_error"]
    assert len(mcp_errors) == 1 and mcp_errors[0]["server"] == "ghost"
    assert mcp_errors[0]["error"]

    trace_file = tmp_path / ".xhx" / "traces" / f"{result.run_id}.jsonl"
    if trace_file.exists():  # trace 是旁路，存在时必须包含 mcp_error 记录
        kinds = [json.loads(line).get("type") for line in trace_file.read_text(encoding="utf-8").splitlines()]
        assert "mcp_error" in kinds


def test_headless_returns_text_without_tools(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    # 模型首轮就直接给出答复、无工具调用 → 循环立即终止。
    client = FakeLLMClient(
        [
            [
                TextDelta("the answer is 42"),
                StreamEnd(stop_reason="end_turn", input_tokens=4, output_tokens=6),
            ],
        ]
    )

    result = run_headless_task(tmp_path, "what is the answer", client=client)

    assert result.status == "completed"
    assert result.summary == "the answer is 42"
    assert client.calls == 1


def test_interactive_run_streams_reply_with_instructions(tmp_path: Path, monkeypatch: Any) -> None:
    """交互式 Agent.run()（TUI 走的路径）：带 XHX.md 时发消息应正常流式回复。

    回归保护：曾因 MemoryManager 缺 load() 方法，TUI 一发消息就崩、无任何回复。
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "XHX.md").write_text("# 项目说明\n测试项目。\n", encoding="utf-8")
    client = FakeLLMClient(
        [
            [
                TextDelta("你好，有什么可以帮你？"),
                StreamEnd(stop_reason="end_turn", input_tokens=3, output_tokens=4),
            ],
        ]
    )
    agent = build_headless_agent(tmp_path, client=client)
    conversation = ConversationManager()
    conversation.add_user_message("你好")

    async def _drive() -> list[str]:
        names: list[str] = []
        async for event in agent.run(conversation):
            names.append(type(event).__name__)
            if len(names) > 50:
                break
        return names

    event_names = asyncio.run(_drive())

    assert "StreamText" in event_names
    assert "LoopComplete" in event_names


def test_headless_missing_profile_returns_error(tmp_path: Path) -> None:
    # 不注入 client → 走 profile 解析；指定不存在的 profile，应返回结构化 error 而非抛异常。
    result = run_headless_task(tmp_path, "do something", profile="nonexistent")

    assert result.status == "error"
    assert "nonexistent" in result.error.lower()


def _write_test_then_done(test_body: str) -> list[list[StreamEvent]]:
    return [
        [
            ToolCallComplete(
                tool_id="t1",
                tool_name="WriteFile",
                arguments={"file_path": "tests/test_x.py", "content": test_body},
            ),
            StreamEnd(stop_reason="tool_use", input_tokens=5, output_tokens=5),
        ],
        [
            TextDelta("done"),
            StreamEnd(stop_reason="end_turn", input_tokens=2, output_tokens=2),
        ],
    ]


def _scaffold_python_project(root: Path) -> None:
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "tests").mkdir(parents=True, exist_ok=True)


def test_headless_verify_reports_passing_tests(tmp_path: Path, monkeypatch: Any) -> None:
    _scaffold_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    client = FakeLLMClient(_write_test_then_done("def test_ok():\n    assert True\n"))

    result = run_headless_task(tmp_path, "add a test", assume_yes=True, verify=True, client=client)

    assert result.status == "completed"
    assert "passed" in result.verification.lower() or "Verification passed" in result.verification


def test_headless_verify_reports_failing_tests(tmp_path: Path, monkeypatch: Any) -> None:
    _scaffold_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    client = FakeLLMClient(_write_test_then_done("def test_bad():\n    assert False\n"))

    result = run_headless_task(tmp_path, "add a test", assume_yes=True, verify=True, client=client)

    assert result.status == "completed"
    assert "FAILED" in result.verification


def test_headless_writes_trace_and_replay_reconstructs(tmp_path: Path, monkeypatch: Any) -> None:
    # 统一 Agent 循环必须落持久化证据链，`xhx replay <run_id>` 能重建轮数/token/回答。
    monkeypatch.chdir(tmp_path)
    client = FakeLLMClient(
        [
            [
                ToolCallComplete(
                    tool_id="t1",
                    tool_name="WriteFile",
                    arguments={"file_path": "note.txt", "content": "hello"},
                ),
                StreamEnd(stop_reason="tool_use", input_tokens=10, output_tokens=5),
            ],
            [
                TextDelta("done"),
                StreamEnd(stop_reason="end_turn", input_tokens=3, output_tokens=2),
            ],
        ]
    )

    result = run_headless_task(tmp_path, "write a note", assume_yes=True, client=client)

    assert result.run_id
    trace_file = tmp_path / ".xhx" / "traces" / f"{result.run_id}.jsonl"
    assert trace_file.exists()
    import json

    types = [json.loads(line)["type"] for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    for expected in ("run_start", "model_turn", "tool_call", "tool_result", "run_end"):
        assert expected in types, f"missing trace entry type: {expected} (got {types})"

    from xhx_agent.evals.replay import TrailReplayer

    replayed = TrailReplayer(tmp_path).replay(result.run_id)
    assert replayed.status == "completed"
    assert replayed.turns == 2
    assert replayed.answer == "done"
    assert replayed.metrics is not None
    assert replayed.metrics.success is True
    assert replayed.metrics.tokens_estimate == 20  # (10+5) + (3+2)，来自真实 usage


def test_headless_result_carries_transcript_messages(tmp_path: Path, monkeypatch: Any) -> None:
    # HeadlessResult.messages 是完整对话的 record 形式，能无损还原成 Message 列表。
    monkeypatch.chdir(tmp_path)
    client = FakeLLMClient(
        [
            [
                TextDelta("the answer is 42"),
                StreamEnd(stop_reason="end_turn", input_tokens=4, output_tokens=6),
            ],
        ]
    )

    result = run_headless_task(tmp_path, "what is the answer", client=client)

    assert result.messages
    from xhx_agent.runtime.session import records_to_messages

    messages = records_to_messages(result.messages)
    assert any(m.role == "user" and "what is the answer" in m.content for m in messages)
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "the answer is 42"


def test_headless_resumes_on_restored_conversation(tmp_path: Path, monkeypatch: Any) -> None:
    # `--resume` 全量还原：把上一轮的完整历史交给 headless，续跑时模型能看到全部旧消息。
    monkeypatch.chdir(tmp_path)
    from xhx_agent.conversation import ConversationManager, Message

    prior = ConversationManager(
        history=[
            Message(role="user", content="first question"),
            Message(role="assistant", content="first answer"),
        ]
    )
    prior.env_injected = True
    prior.ltm_injected = True

    seen_histories: list[int] = []

    class RecordingClient(FakeLLMClient):
        async def stream(self, conversation: Any, system: str = "", tools: Any = None) -> AsyncIterator[StreamEvent]:
            seen_histories.append(len(conversation.get_messages()))
            async for event in super().stream(conversation, system, tools):
                yield event

    client = RecordingClient(
        [[TextDelta("continued"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)]]
    )

    result = run_headless_task(tmp_path, "follow up", client=client, conversation=prior)

    assert result.status == "completed"
    assert result.summary == "continued"
    # 模型看到的历史 = 旧 2 条 + 新 user 任务 1 条
    assert seen_histories and seen_histories[0] == 3
