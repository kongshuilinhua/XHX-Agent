from xhx_agent.orchestrators.registry import select_orchestrator
from xhx_agent.runtime.app import RuntimeApp


def test_plan_registry() -> None:
    assert select_orchestrator("plan").name == "plan"


def test_plan_conversational_no_changes(tmp_path, monkeypatch) -> None:
    import xhx_agent.orchestrators.plan as planmod
    from xhx_agent.models.types import ChatResult

    class _Fake:
        def chat(self, messages, tools):
            return ChatResult(content="Nothing to change; here is my analysis.", tool_calls=[])

    monkeypatch.setattr(planmod, "build_chat_client", lambda profile: _Fake())
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("explain this repo", profile_name="mock", mode="plan")

    assert res.status == "success"
    assert res.mode == "plan"
    assert res.answer
    assert res.changed_files == []
    # No changes -> verification is a skip class, not a real pass/fail.
    assert res.verification in {"skipped_no_changes", "not_executed"}
