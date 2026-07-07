from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from xhx_agent.cli.rpc import start_rpc_loop
from xhx_agent.evals.benchmark import BenchmarkRunner
from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evals.replay import TrailReplayer
from xhx_agent.runtime.config import write_default_config
from xhx_agent.runtime.profiles import write_default_profiles


def test_run_metrics_model() -> None:
    m = RunMetrics(
        duration_seconds=12.34,
        turns=3,
        tokens_estimate=5000,
        files_changed_count=2,
        commands_run_count=1,
        repair_attempts=0,
        success=True,
    )
    assert m.duration_seconds == 12.34
    assert m.turns == 3
    assert m.tokens_estimate == 5000
    assert m.files_changed_count == 2
    assert m.commands_run_count == 1
    assert m.repair_attempts == 0
    assert m.success is True


def test_trail_replayer(tmp_path: Path) -> None:
    run_id = "test-replay-run"
    traces_dir = tmp_path / ".xhx" / "traces"
    traces_dir.mkdir(parents=True)
    trace_file = traces_dir / f"{run_id}.jsonl"

    # Write a mock trace
    entries = [
        {"type": "run_start", "run_id": run_id, "payload": {"task": "Mock task for replay"}},
        {"type": "context_pack", "run_id": run_id, "payload": {"used_tokens_estimate": 1500}},
        {"type": "model_plan", "run_id": run_id, "payload": {"summary": "Step 1: Read config"}},
        {"type": "context_pack", "run_id": run_id, "payload": {"used_tokens_estimate": 2500}},
        {"type": "model_plan", "run_id": run_id, "payload": {"summary": "Step 2: Done"}},
        {
            "type": "verification",
            "run_id": run_id,
            "payload": {
                "command": "pytest",
                "status": "success",
                "exit_code": 0,
                "summary": "all tests passed",
                "policy": {"decision": "allow", "risk": "confirm", "reason": "pytest is standard"},
            },
        },
        {
            "type": "run_end",
            "run_id": run_id,
            "payload": {
                "status": "success",
                "changed_files": ["app.py"],
                "commands": ["pytest"],
                "verification": "passed",
                "checkpoint_path": "checkpoint-path-123",
                "restore_plan_path": None,
                "repair_attempts": 0,
                "risk_summary": [],
                "duration_seconds": 4.56,
            },
        },
    ]
    with open(trace_file, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    replayer = TrailReplayer(tmp_path)
    result = replayer.replay(run_id)

    # Verify RunResult properties
    assert result.run_id == run_id
    assert result.status == "success"
    assert result.turns == 2
    assert result.changed_files == ["app.py"]
    assert result.commands == ["pytest"]
    assert result.verification == "passed"
    assert result.checkpoint_path == "checkpoint-path-123"
    assert result.restore_plan_path is None
    assert result.metrics is not None
    assert result.metrics.turns == 2
    assert result.metrics.tokens_estimate == 4000
    assert result.metrics.duration_seconds == 4.56

    # Verify report is written to logbook folder
    report_file = tmp_path / ".xhx" / "logbook" / f"{run_id}.md"
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Mock task for replay" in content
    assert "Step 1: Read config" in content
    assert "app.py" in content


def test_trail_replayer_missing_trace_raises(tmp_path: Path) -> None:
    # 没有 trace 的 run（如新栈 headless run）不该静默返回全零 success。
    with pytest.raises(FileNotFoundError, match="no-such-run"):
        TrailReplayer(tmp_path).replay("no-such-run")


def test_benchmark_runner(tmp_path: Path) -> None:
    # Initialize a mock environment
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    runner = BenchmarkRunner(tmp_path)
    results = runner.run_benchmark("mock")

    assert len(results) == 3
    for r in results:
        assert r.profile == "mock"
        assert r.success is True
        assert r.turns >= 1
        assert r.duration_seconds > 0.0


def test_jsonl_rpc_loop(monkeypatch, tmp_path: Path) -> None:
    # Set up mock input stream
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "init", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "repo-index", "params": {"refresh": True}},
        {"jsonrpc": "2.0", "id": 3, "method": "exit", "params": {}},
    ]
    input_data = "\n".join(json.dumps(r) for r in reqs) + "\n"

    stdin_mock = StringIO(input_data)
    stdout_mock = StringIO()

    monkeypatch.setattr(sys, "stdin", stdin_mock)
    monkeypatch.setattr(sys, "stdout", stdout_mock)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    # Initialize a mock environment
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    # Run the RPC loop
    start_rpc_loop()

    # Verify standard output has correct JSONL responses
    lines = stdout_mock.getvalue().strip().split("\n")
    assert len(lines) >= 3

    resp1 = json.loads(lines[0])
    assert resp1["jsonrpc"] == "2.0"
    assert resp1["id"] == 1
    assert "config_created" in resp1["result"]

    resp2 = json.loads(lines[1])
    assert resp2["jsonrpc"] == "2.0"
    assert resp2["id"] == 2
    assert "status" in resp2["result"]

    resp3 = json.loads(lines[2])
    assert resp3["jsonrpc"] == "2.0"
    assert resp3["id"] == 3
    assert resp3["result"] == "Goodbye"
