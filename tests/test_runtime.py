from pathlib import Path
import json
import shutil
import subprocess

from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile, profiles_path
from xhx_agent.models.types import ModelPlan, ToolStep
from xhx_agent.tools.registry import ToolRegistry, ToolExecutionResult
from xhx_agent.context.pack import ContextPack
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.repair import MAX_REPAIR_ATTEMPTS
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


def test_run_task_emits_runtime_events(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    events = []

    result = RuntimeApp(tmp_path).run_task("analyze this repo", event_callback=events.append)

    assert result.status == "success"
    event_types = [event.type for event in events]
    assert "run_start" in event_types
    assert "scan" in event_types
    assert "context_pack" in event_types
    assert "model_plan" in event_types
    assert "run_end" in event_types


def test_run_task_cancels_before_model_loop(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    events = []

    result = RuntimeApp(tmp_path).run_task("analyze this repo", event_callback=events.append, cancel_check=lambda: True)

    assert result.status == "cancelled"
    assert result.verification == "cancelled"
    assert result.changed_files == []
    assert any(event.type == "run_cancelled" for event in events)
    trace_files = list((tmp_path / ".xhx" / "traces").glob("*.jsonl"))
    trace_lines = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(item["type"] == "cancel_requested" for item in trace_lines)


def test_python_fixture_mock_closed_loop(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()
    result = RuntimeApp(workspace).run_task("fix failing test", assume_yes=True)
    assert result.status == "success"
    assert result.verification == "passed"
    assert result.changed_files == ["src/calc.py"]
    assert result.checkpoint_path is not None
    assert (workspace / result.checkpoint_path).exists()
    assert result.repair is not None
    assert not result.repair.should_repair
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")
    trace_files = list((workspace / ".xhx" / "traces").glob("*.jsonl"))
    evidence_files = list((workspace / ".xhx" / "evidence").glob("*.jsonl"))
    assert trace_files
    assert evidence_files
    trace_lines = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    evidence_lines = [json.loads(line) for line in evidence_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(item["type"] == "checkpoint" for item in trace_lines)
    assert any(item["type"] == "context_debug_report" for item in trace_lines)
    patch_binding = next(item for item in trace_lines if item["type"] == "patch_evidence_binding")
    assert patch_binding["payload"]["changed_files"] == ["src/calc.py"]
    assert any(item["type"] == "policy_decision" for item in trace_lines)
    assert any(item["type"] == "repair_decision" for item in trace_lines)
    patch_evidence = next(item for item in evidence_lines if item["kind"] == "patch")
    assert patch_binding["payload"]["evidence_id"] == patch_evidence["id"]
    assert any(item["kind"] == "test" for item in evidence_lines)
    assert any(item["kind"] == "checkpoint" for item in evidence_lines)
    assert any(item["kind"] == "policy" for item in evidence_lines)
    report = (workspace / result.summary_path).read_text(encoding="utf-8")
    assert "## Verification Details" in report
    assert "## Checkpoint" in report
    assert "## Repair" in report
    assert "artifact_ref: trace://" in report
    assert f"`{patch_evidence['id']}` patch:apply_patch" in report
    assert "exit_code: 0" in report
    assert list((workspace / ".xhx" / "context").glob("*.json"))


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
    assert result.checkpoint_path is not None
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


def test_runtime_manual_verification_runs_for_changed_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    RuntimeApp(tmp_path).init_project()

    terminal_result = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        exit_code=0,
        summary="passed",
    )
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: terminal_result)
    events = []

    result = RuntimeApp(tmp_path).verify_changed_files(["demo.py"], assume_yes=True, event_callback=events.append)

    assert result.status == "passed"
    assert result.commands == ["python -m pytest"]
    assert result.verification_results == [terminal_result]
    assert result.summary_path is not None
    assert (tmp_path / result.summary_path).exists()
    assert any(event.type == "verification_start" for event in events)
    assert any(event.type == "verification_result" for event in events)


def test_runtime_manual_verification_skips_without_changed_files(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()

    result = RuntimeApp(tmp_path).verify_changed_files([])

    assert result.status == "skipped_no_changes"
    assert result.commands == []
    assert result.summary_path is not None
    assert (tmp_path / result.summary_path).exists()


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

    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: failed_result)

    result = RuntimeApp(workspace).run_task("fix failing test", assume_yes=True)

    assert result.status == "failed"
    assert result.verification == "failed"
    assert result.verification_results == [failed_result]
    assert result.repair is not None
    assert not result.repair.should_repair
    assert result.restore_plan_path is not None
    assert (workspace / result.restore_plan_path).exists()
    assert "not enabled" in result.repair.reason
    assert any("exit_code=1" in risk for risk in result.risk_summary)
    assert any("Repair not attempted" in risk for risk in result.risk_summary)
    report = (workspace / result.summary_path).read_text(encoding="utf-8")
    assert "assert 1 == 2" in report
    assert "exit_code: 1" in report
    assert "## Restore Plan" in report
    assert str(result.restore_plan_path) in report
    assert "Auto repair is not enabled" in report


def test_runtime_auto_repair_attempts_second_patch(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
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
    plans = [
        ModelPlan(
            summary="make initial wrong change",
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
        ),
        ModelPlan(
            summary="repair change",
            steps=[
                ToolStep(
                    tool="apply_patch",
                    arguments={
                        "patch": """*** Begin Patch
*** Update File: demo.py
@@
-value = 2
+value = 3
*** End Patch
"""
                    },
                )
            ],
        ),
    ]

    def fake_build_plan(_task: str, _profile: ModelProfile, _context: ContextPack) -> ModelPlan:
        return plans.pop(0)

    verification_results = [
        TerminalResult(
            command="python -m pytest",
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="expected value 3",
        ),
        TerminalResult(
            command="python -m pytest",
            status="success",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=0,
            summary="passed",
        ),
    ]

    app._build_plan = fake_build_plan  # type: ignore[method-assign]
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: verification_results.pop(0))

    result = app.run_task("fix demo", profile_name="real", assume_yes=True, auto_repair=True)

    assert result.status == "success"
    assert result.verification == "passed"
    assert result.repair_attempts == 1
    assert result.repair is not None
    assert not result.repair.should_repair
    assert result.repair.reason == "Repair is only considered after failed verification."
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 3\n"


def test_runtime_manual_repair_runs_one_attempt(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
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

    app._build_plan = lambda _task, _profile, _context: ModelPlan(  # type: ignore[method-assign]
        summary="repair value",
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
    failed_result = TerminalResult(
        command="python -m pytest",
        status="failed",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        exit_code=1,
        summary="expected value 2",
    )
    passed_result = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        exit_code=0,
        summary="passed",
    )
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: passed_result)
    events = []

    result = app.repair_after_failed_verification(
        task="fix demo",
        changed_files=["demo.py"],
        failed_verification_results=[failed_result],
        profile_name="real",
        assume_yes=True,
        event_callback=events.append,
    )

    assert result.status == "success"
    assert result.verification == "passed"
    assert result.repair_attempts == 1
    assert result.commands == ["python -m pytest"]
    assert result.summary_path is not None
    assert (tmp_path / result.summary_path).exists()
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 2\n"
    assert any(event.type == "repair_start" for event in events)
    assert any(event.type == "verification_result" for event in events)


def test_runtime_manual_repair_loop_can_run_second_attempt(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
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
    values = [2, 3]

    def fake_build_plan(_task: str, _profile: ModelProfile, _context: ContextPack) -> ModelPlan:
        next_value = values.pop(0)
        return ModelPlan(
            summary=f"set value to {next_value}",
            steps=[
                ToolStep(
                    tool="apply_patch",
                    arguments={
                        "patch": f"""*** Begin Patch
*** Update File: demo.py
@@
-value = {next_value - 1}
+value = {next_value}
*** End Patch
"""
                    },
                )
            ],
        )

    verification_results = [
        TerminalResult(
            command="python -m pytest",
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="expected value 3",
        ),
        TerminalResult(
            command="python -m pytest",
            status="success",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=0,
            summary="passed",
        ),
    ]
    app._build_plan = fake_build_plan  # type: ignore[method-assign]
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: verification_results.pop(0))

    result = app.repair_after_failed_verification(
        task="fix demo",
        changed_files=["demo.py"],
        failed_verification_results=[
            TerminalResult(
                command="python -m pytest",
                status="failed",
                policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
                exit_code=1,
                summary="expected value 3",
            )
        ],
        profile_name="real",
        assume_yes=True,
        max_attempts=2,
    )

    assert result.status == "success"
    assert result.verification == "passed"
    assert result.repair_attempts == 2
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 3\n"


def test_runtime_manual_repair_requires_failed_verification(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    passed_result = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        exit_code=0,
        summary="passed",
    )

    result = RuntimeApp(tmp_path).repair_after_failed_verification(
        task="fix demo",
        changed_files=["demo.py"],
        failed_verification_results=[passed_result],
        assume_yes=True,
    )

    assert result.status == "skipped_no_failed_verification"
    assert result.repair_attempts == 0
    assert any("requires a failed verification" in risk for risk in result.risk_summary)


def test_runtime_auto_repair_stops_at_attempt_limit(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
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
    values = [2, 3, 4]

    def fake_build_plan(_task: str, _profile: ModelProfile, _context: ContextPack) -> ModelPlan:
        next_value = values.pop(0)
        previous_value = next_value - 1
        return ModelPlan(
            summary=f"set value to {next_value}",
            steps=[
                ToolStep(
                    tool="apply_patch",
                    arguments={
                        "patch": f"""*** Begin Patch
*** Update File: demo.py
@@
-value = {previous_value}
+value = {next_value}
*** End Patch
"""
                    },
                )
            ],
        )

    def always_fail(_workspace: Path, command: str, *_args, **_kwargs) -> TerminalResult:
        return TerminalResult(
            command=command,
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="still failing",
        )

    app._build_plan = fake_build_plan  # type: ignore[method-assign]
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", always_fail)

    result = app.run_task("fix demo", profile_name="real", assume_yes=True, auto_repair=True)

    assert result.status == "failed"
    assert result.verification == "failed"
    assert result.repair_attempts == MAX_REPAIR_ATTEMPTS
    assert result.repair is not None
    assert not result.repair.should_repair
    assert result.restore_plan_path is not None
    assert result.repair.reason == "Repair attempt limit reached."
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 4\n"
    report = (tmp_path / result.summary_path).read_text(encoding="utf-8")
    assert "Repair attempt limit reached." in report
    assert f"repair_attempts: {MAX_REPAIR_ATTEMPTS}" in report
    trace_files = list((tmp_path / ".xhx" / "traces").glob("*.jsonl"))
    evidence_files = list((tmp_path / ".xhx" / "evidence").glob("*.jsonl"))
    trace_lines = [json.loads(line) for line in trace_files[0].read_text(encoding="utf-8").splitlines()]
    evidence_lines = [json.loads(line) for line in evidence_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(item["type"] == "repair_decision" for item in trace_lines)
    assert any(item["type"] == "restore_plan" for item in trace_lines)
    assert any(item["kind"] == "decision" and item["source"] == "repair" for item in evidence_lines)


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
    assert sum(1 for item in trace_lines if item["type"] == "context_debug_report") == 2
    assert any(item["type"] == "verification_skipped" for item in trace_lines)


def test_runtime_emits_model_delta_events_for_streaming_profiles(tmp_path: Path, monkeypatch) -> None:
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
                    stream=True,
                )
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    app = RuntimeApp(tmp_path)

    def fake_plan(_self, _task, _context, delta_callback=None):
        if delta_callback:
            delta_callback('{"summary":"streamed')
            delta_callback(' plan","status":"done","steps":[]}')
        return ModelPlan(summary="streamed plan", status="done", steps=[])

    monkeypatch.setattr("xhx_agent.runtime.app.OpenAICompatibleClient.plan", fake_plan)
    events = []

    result = app.run_task("analyze", profile_name="real", event_callback=events.append)

    assert result.status == "success"
    assert [event.message for event in events if event.type == "model_delta"] == [
        '{"summary":"streamed',
        ' plan","status":"done","steps":[]}',
    ]


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


def test_runtime_diff_changed_files_returns_read_only_git_diff(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "demo.txt"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "demo.txt").write_text("new\n", encoding="utf-8")

    result = RuntimeApp(tmp_path).diff_changed_files(["demo.txt"])

    assert result.changed_files == ["demo.txt"]
    assert result.summary == "1 changed file(s)."
    assert result.truncated is False
    assert "diff --git" in result.diff_text
    assert "-old" in result.diff_text
    assert "+new" in result.diff_text


def test_runtime_diff_changed_files_truncates_large_diff(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "demo.txt"], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / "demo.txt").write_text("new line\n" * 20, encoding="utf-8")

    result = RuntimeApp(tmp_path).diff_changed_files(["demo.txt"], max_chars=80)

    assert result.changed_files == ["demo.txt"]
    assert len(result.diff_text) == 80
    assert result.truncated is True
    assert any("truncated" in risk for risk in result.risk_summary)
