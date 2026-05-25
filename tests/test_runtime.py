from pathlib import Path
import json
import shutil

from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile, profiles_path
from xhx_agent.models.types import ModelPlan, ToolStep
from xhx_agent.tools.registry import ToolRegistry, ToolExecutionResult


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
    app._build_plan = lambda _task, _profile, _summary: ModelPlan(  # type: ignore[method-assign]
        summary="bad model plan",
        steps=[ToolStep(tool="terminal", arguments={"command": "python -m pytest"})],
    )

    result = app.run_task("bad plan")

    assert result.status == "failed"
    assert not executed
    assert any("unsupported tool" in risk.lower() for risk in result.risk_summary)
