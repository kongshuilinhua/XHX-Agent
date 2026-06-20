"""长尾模块单测：teams/transcript、agents/loader、agents/tool_filter。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

from xhx_agent.agents.loader import AgentLoader
from xhx_agent.agents.tool_filter import (
    _get_schemas,
    build_filtered_registry,
    resolve_agent_tools,
)
from xhx_agent.conversation import ConversationManager, Message, ToolResultBlock, ToolUseBlock
from xhx_agent.teams.transcript import (
    _deserialize_conversation,
    _serialize_conversation,
    load_transcript,
    save_transcript,
)
from xhx_agent.tools import ToolRegistry
from xhx_agent.tools.base import Tool, ToolResult

# --- teams/transcript ---


def _sample_conv() -> ConversationManager:
    conv = ConversationManager()
    conv.history = [
        Message(role="user", content="做个东西"),
        Message(
            role="assistant",
            content="好",
            tool_uses=[ToolUseBlock(tool_use_id="t1", tool_name="Write", arguments={"p": "a"})],
        ),
        Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="t1", content="ok")]),
    ]
    return conv


def test_transcript_serialize_roundtrip() -> None:
    data = _serialize_conversation(_sample_conv())
    conv2 = _deserialize_conversation(data)
    assert [m.role for m in conv2.history] == ["user", "assistant", "user"]
    assert conv2.history[1].tool_uses[0].tool_name == "Write"
    assert conv2.history[2].tool_results[0].content == "ok"


def test_transcript_save_load(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("xhx_agent.teams.models.resolve_team_dir", lambda name: tmp_path / name)
    path = save_transcript("teamA", "agent1", _sample_conv())
    assert path.is_file()
    loaded = load_transcript("teamA", "agent1")
    assert loaded is not None and len(loaded.history) == 3
    # 不存在 → None
    assert load_transcript("teamA", "ghost") is None


# --- agents/loader ---


def _write_agent(dir_: Path, name: str, desc: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n你是 {name}。\n", encoding="utf-8")


def test_agent_loader_load_all_and_get(tmp_path: Path) -> None:
    _write_agent(tmp_path / ".xhx" / "agents", "tester", "用于测试")
    loader = AgentLoader(str(tmp_path))
    agents = loader.load_all()
    assert "tester" in agents
    got = loader.get("tester")
    assert got is not None and got.agent_type == "tester"
    assert ("tester", "用于测试") in loader.list_agents()
    # 内置 agent 也应被加载（数量 > 1）
    assert len(agents) >= 1
    # 未知 agent → None
    assert loader.get("nope") is None


def test_agent_loader_missing_dir(tmp_path: Path) -> None:
    loader = AgentLoader(str(tmp_path))
    # 无项目 agents 目录也不报错，至少有内置
    agents = loader.load_all()
    assert isinstance(agents, dict)


# --- agents/tool_filter ---


class _P(BaseModel):
    x: str = ""


def _tool(tool_name: str) -> Tool:
    class _T(Tool):
        name = tool_name
        description = f"{tool_name} desc"
        params_model = _P
        category = "read"

        async def execute(self, params: _P) -> ToolResult:  # type: ignore[override]
            return ToolResult(output="ok")

    return _T()


def _filter_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for n in ("echo", "ReadFile", "Agent", "present_plan"):
        reg.register(_tool(n))
    return reg


def test_resolve_agent_tools_blacklist() -> None:
    reg = _filter_registry()
    defn = SimpleNamespace(source="builtin", disallowed_tools=[], tools=[])
    names = {s["function"]["name"] for s in resolve_agent_tools(reg, defn)}
    # 全局黑名单移除 Agent / present_plan
    assert "Agent" not in names and "present_plan" not in names
    assert "echo" in names and "ReadFile" in names


def test_resolve_agent_tools_whitelist_and_disallow() -> None:
    reg = _filter_registry()
    wl = SimpleNamespace(source="builtin", disallowed_tools=[], tools=["echo"])
    names = {s["function"]["name"] for s in resolve_agent_tools(reg, wl)}
    assert names == {"echo"}

    dis = SimpleNamespace(source="builtin", disallowed_tools=["echo"], tools=[])
    names2 = {s["function"]["name"] for s in resolve_agent_tools(reg, dis)}
    assert "echo" not in names2 and "ReadFile" in names2


def test_build_filtered_registry() -> None:
    reg = _filter_registry()
    defn = SimpleNamespace(source="project", disallowed_tools=[], tools=["echo", "ReadFile"])
    filtered = build_filtered_registry(reg, defn)
    assert filtered.get("echo") is not None
    assert filtered.get("Agent") is None  # 被过滤


def test_get_schemas_fallback() -> None:
    assert _get_schemas(object()) == []  # 无相关方法 → 空
    assert isinstance(_get_schemas(_filter_registry()), list)
