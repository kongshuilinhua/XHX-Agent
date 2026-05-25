from pathlib import Path
import json
import shutil

from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile, profiles_path
from xhx_agent.models.types import ModelPlan, ToolStep
from xhx_agent.tools.registry import ToolRegistry, ToolExecutionResult
from xhx_agent.context.pack import ContextPack
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel


def test_init_project_writes_expected_files(tmp_path: Path) -> None:
    result = RuntimeApp(tmp_path).init_project()
    assert result.config_created
    assert result.profiles_created
    assert result.xhx_md_created
    assert (tmp_path / ".xhx" / "config.json").exists()
    assert (tmp_path / "XHX.md").exists()


def test_run_task_writes_report(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    result = RuntimeApp(tmp_path).run_task("analyze this repo")
    assert result.status == "success"
    assert result.verification == "skipped_no_changes"
    assert (tmp_path / result.summary_path).exists()


def test_python_fixture_mock_closed_loop(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()
    result = RuntimeApp(workspace).run_task("fix failing test", assume_yes=True)
    assert result.status == "success"
    assert result.verification == "passed"
    assert result.changed_files == ["src/calc.py"]
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")
    trace_files = list((workspace / ".xhx" / "traces").glob("*.jsonl"))
    evidence_files = list((workspace / ".xhx" / "evidence").glob("*.jsonl"))
    assert trace_files
    assert evidence_files
    evidence_lines = [json.loads(line) for line in evidence_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(item["kind"] == "patch" for item in evidence_lines)
    assert any(item["kind"] == "test" for item in evidence_lines)
    report = (workspace / result.summary_path).read_text(encoding="utf-8")
    assert "## Verification Details" in report
    assert "exit_code: 0" in report


def test_node_fixture_mock_closed_loop(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "node_bug"
    workspace = tmp_path / "node_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()
    result = RuntimeApp(workspace).run_task("fix failing test", assume_yes=True)
    assert result.status == "success"
    assert result.verification == "passed"
    assert result.changed_files == ["src/index.js"]
    assert "return a + b;" in (workspace / "src" / "index.js").read_text(encoding="utf-8")


def test_runtime_requires_confirmation_without_yes(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()

    result = RuntimeApp(workspace).run_task("fix failing test")

    assert result.status == "success"
    assert result.verification == "requires_confirmation"
    assert result.commands == ["uv run pytest"]
    assert result.verification_results[0].status == "confirm"
    assert any("requires confirmation" in risk for risk in result.risk_summary)


def test_runtime_confirmation_callback_executes_verification(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()

    result = RuntimeApp(workspace).run_task(
        "fix failing test",
        confirm_callback=lambda _command, _decision: True,
    )

    assert result.status == "success"
    assert result.verification == "passed"
    assert result.verification_results[0].exit_code == 0


def test_runtime_failed_verification_stops_and_reports(tmp_path: Path, monkeypatch) -> None:
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()

    failed_result = TerminalResult(
        command="uv run pytest",
        status="failed",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        stdout="",
        stderr="assert 1 == 2\nline 1\n",
        exit_code=1,
        summary="assert 1 == 2\nline 1",
    )

    monkeypatch.setattr("xhx_agent.runtime.app.run_terminal", lambda *_args, **_kwargs: failed_result)

    result = RuntimeApp(workspace).run_task("fix failing test", assume_yes=True)

    assert result.status == "failed"
    assert result.verification == "failed"
    assert result.verification_results == [failed_result]
    assert any("exit_code=1" in risk for risk in result.risk_summary)
    report = (workspace / result.summary_path).read_text(encoding="utf-8")
    assert "assert 1 == 2" in report
    assert "exit_code: 1" in report


def test_openai_profile_missing_api_key_fails_safely(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    profiles_path(tmp_path).write_text(
        ProfilesFile(
            profiles=[
                ModelProfile(
                    name="real",
                    provider="openai-compatible",
                    base_url="https://api.example.com/v1",
                    api_key_env="XHX_TEST_MISSING_API_KEY",
                    model="demo-model",
                    stream=False,
                )
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    result = RuntimeApp(tmp_path).run_task("analyze this repo", profile_name="real")

    assert result.status == "failed"
    assert result.verification == "not_executed"
    assert result.changed_files == []
    assert any("XHX_TEST_MISSING_API_KEY" in risk for risk in result.risk_summary)
    trace_files = list((tmp_path / ".xhx" / "traces").glob("*.jsonl"))
    trace_lines = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(item["type"] == "model_error" for item in trace_lines)


def test_runtime_rejects_invalid_model_plan_before_tool_execution(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    registry = ToolRegistry()
    executed = False

    def fake_runner(_context, _arguments) -> ToolExecutionResult:
        nonlocal executed
        executed = True
        return ToolExecutionResult(
            tool="search",
            status="success",
            summary="should not run",
            trace_payload={"tool": "search"},
        )

    registry.register("search", fake_runner)
    app = RuntimeApp(tmp_path, tool_registry=registry)
    app._build_plan = lambda _task, _profile, _context: ModelPlan(  # type: ignore[method-assign]
        summary="bad model plan",
        steps=[ToolStep(tool="terminal", arguments={"command": "python -m pytest"})],
    )

    result = app.run_task("bad plan")

    assert result.status == "failed"
    assert not executed
    assert any("unsupported tool" in risk.lower() for risk in result.risk_summary)


def test_runtime_feeds_tool_results_into_next_model_turn(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    profiles_path(tmp_path).write_text(
        ProfilesFile(
            profiles=[
                ModelProfile(
                    name="real",
                    provider="openai-compatible",
                    base_url="https://api.example.com/v1",
                    api_key_env="XHX_TEST_API_KEY",
                    model="demo-model",
                    stream=False,
                )
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    contexts: list[ContextPack] = []
    app = RuntimeApp(tmp_path)

    def fake_build_plan(_task: str, _profile: ModelProfile, context: ContextPack) -> ModelPlan:
        contexts.append(context)
        if len(contexts) == 1:
            return ModelPlan(
                summary="read readme first",
                steps=[ToolStep(tool="read_file", arguments={"path": "README.md"})],
            )
        assert any(item.kind == "tool_results" and "read_file" in item.content for item in context.items)
        return ModelPlan(summary="analysis complete", status="done", steps=[])

    app._build_plan = fake_build_plan  # type: ignore[method-assign]

    result = app.run_task("analyze README", profile_name="real")

    assert result.status == "success"
    assert result.turns == 2
    assert len(contexts) == 2
    trace_files = list((tmp_path / ".xhx" / "traces").glob("*.jsonl"))
    trace_lines = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    assert sum(1 for item in trace_lines if item["type"] == "context_pack") == 2
    assert any(item["type"] == "verification_skipped" for item in trace_lines)


def test_runtime_stops_when_real_model_exceeds_max_turns(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    profiles_path(tmp_path).write_text(
        ProfilesFile(
            profiles=[
                ModelProfile(
                    name="real",
                    provider="openai-compatible",
                    base_url="https://api.example.com/v1",
                    api_key_env="XHX_TEST_API_KEY",
                    model="demo-model",
                    stream=False,
                )
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    app = RuntimeApp(tmp_path)

    def fake_build_plan(_task: str, _profile: ModelProfile, _context: ContextPack) -> ModelPlan:
        return ModelPlan(
            summary="keep searching",
            steps=[ToolStep(tool="search", arguments={"query": "never-done"})],
        )

    app._build_plan = fake_build_plan  # type: ignore[method-assign]

    result = app.run_task("analyze forever", profile_name="real")

    assert result.status == "failed"
    assert result.turns == 4
    assert result.verification == "not_executed"
    assert any("did not finish" in risk for risk in result.risk_summary)


def test_preview_plan_does_not_execute_tools(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    app = RuntimeApp(tmp_path)

    def fake_build_plan(_task: str, _profile: ModelProfile, _context: ContextPack) -> ModelPlan:
        return ModelPlan(
            summary="would patch file",
            steps=[
                ToolStep(
                    tool="apply_patch",
                    arguments={
                        "patch": """*** Begin Patch
*** Update File: demo.py
@@
-value = 1
+value = 2
*** End Patch
"""
                    },
                )
            ],
        )

    app._build_plan = fake_build_plan  # type: ignore[method-assign]

    result = app.preview_plan("change demo")

    assert result.status == "success"
    assert result.step_count == 1
    assert "value = 1" in (tmp_path / "demo.py").read_text(encoding="utf-8")
    assert (tmp_path / result.trace_path).exists()
