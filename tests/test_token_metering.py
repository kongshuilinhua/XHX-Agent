from unittest.mock import MagicMock

from xhx_agent.orchestrators._toolturn import _estimate_message_tokens, chat_and_count


def test_estimate_message_tokens_positive():
    # 1. content > 0
    msgs1 = [{"role": "user", "content": "hello world"}]
    assert _estimate_message_tokens(msgs1) > 0

    # 2. Empty/None message returns 0
    msgs2 = []
    assert _estimate_message_tokens(msgs2) == 0
    msgs2_alt = [{"role": "user", "content": None}]
    assert _estimate_message_tokens(msgs2_alt) == 0

    # 3. tool_calls arguments count
    msgs3 = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"arguments": '{"query":"test"}'}}]}]
    assert _estimate_message_tokens(msgs3) > 0


def test_chat_and_count_accumulates():
    ctx = MagicMock()
    ctx.metrics_tracker = {"tokens": 0}

    class FakeClient:
        def chat(self, messages, schemas):
            return "fake response"

    client = FakeClient()
    messages = [{"role": "user", "content": "count this string"}]
    schemas = []

    res = chat_and_count(ctx, client, messages, schemas)
    assert res == "fake response"
    assert ctx.metrics_tracker["tokens"] > 0


def test_loop_sets_token_metrics(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def chat(self, messages, tools):
            return ChatResult(content="Done task in loop", tool_calls=[])

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: FakeClient())

    res = RuntimeApp(tmp_path).run_task("do task", profile_name="mock", mode="loop")

    assert res.status == "success"
    assert res.metrics is not None
    assert res.metrics.tokens_estimate > 0


def test_plan_sets_token_metrics(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.plan as planmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            from xhx_agent.models.types import ToolCall
            if self.i == 0:
                self.i += 1
                return ChatResult(
                    content=None,
                    tool_calls=[ToolCall(id="p1", name="present_plan", arguments={"plan": "My plan", "files_to_change": []})]
                )
            return ChatResult(content="Done task in plan", tool_calls=[])

    monkeypatch.setattr(planmod, "build_chat_client", lambda profile: FakeClient())

    res = RuntimeApp(tmp_path).run_task("do task", profile_name="mock", mode="plan", assume_yes=True)

    assert res.status == "success"
    assert res.metrics is not None
    assert res.metrics.tokens_estimate > 0


def test_benchmark_token_metering_with_modes(tmp_path):
    from xhx_agent.evals.benchmark import BenchmarkRunner, render_benchmark_report
    from xhx_agent.runtime.config import write_default_config
    from xhx_agent.runtime.profiles import write_default_profiles

    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    runner = BenchmarkRunner(tmp_path)
    results = runner.run_matrix("mock", ["loop", "plan", "graph"])

    report = render_benchmark_report("mock", results)

    loop_tokens = report.summary["loop"]["mean_tokens"]
    plan_tokens = report.summary["plan"]["mean_tokens"]
    graph_tokens = report.summary["graph"]["mean_tokens"]

    assert loop_tokens > 0
    assert plan_tokens > 0
    assert graph_tokens > 0
    # graph mode could be smaller because planner sends far fewer tools than loop mode
    assert graph_tokens > 0
