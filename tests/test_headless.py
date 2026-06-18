"""headless 驱动测试：用脚本化的假模型客户端驱动统一 Agent 循环跑到完成。

同时作为新栈（agent_runner.Agent）的第一个端到端测试夹具。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

from xhx_agent.client import LLMClient
from xhx_agent.runtime.headless import run_headless_task
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


def test_headless_missing_profile_returns_error(tmp_path: Path) -> None:
    # 不注入 client → 走 profile 解析；指定不存在的 profile，应返回结构化 error 而非抛异常。
    result = run_headless_task(tmp_path, "do something", profile="nonexistent")

    assert result.status == "error"
    assert "nonexistent" in result.error.lower()
