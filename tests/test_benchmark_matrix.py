import json
from pathlib import Path

from typer.testing import CliRunner

from xhx_agent.cli.main import app
from xhx_agent.evals.benchmark import BenchmarkResult, BenchmarkRunner, render_benchmark_report
from xhx_agent.runtime.config import write_default_config
from xhx_agent.runtime.profiles import write_default_profiles


def test_run_matrix_scale(tmp_path: Path):
    # Initialize a mock environment
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    runner = BenchmarkRunner(tmp_path)

    # 1. Custom modes
    results1 = runner.run_matrix("mock", ["loop", "plan"])
    # 3 fixtures * 2 modes = 6 results
    assert len(results1) == 6
    for r in results1:
        assert r.mode in ["loop", "plan"]
        assert r.success is True

    # 2. Default modes
    results2 = runner.run_matrix("mock")
    # 3 fixtures * 3 modes = 9 results
    assert len(results2) == 9
    for r in results2:
        assert r.mode in ["loop", "plan", "team"]


def test_render_benchmark_report_aggregation():
    # 2. Aggregation correctness: success_rate, runs, means, etc.
    # Hand-constructed BenchmarkResult list
    results = [
        BenchmarkResult(
            fixture_id="f1",
            name="fixture 1",
            profile="mock",
            status="success",
            turns=3,
            duration_seconds=10.0,
            tokens_estimate=1000,
            success=True,
            mode="loop",
            files_changed=2,
            repair_attempts=1,
        ),
        BenchmarkResult(
            fixture_id="f2",
            name="fixture 2",
            profile="mock",
            status="failed",
            turns=1,
            duration_seconds=5.0,
            tokens_estimate=500,
            success=False,
            mode="loop",
            files_changed=0,
            repair_attempts=0,
        ),
        BenchmarkResult(
            fixture_id="f3",
            name="fixture 3",
            profile="mock",
            status="success",
            turns=4,
            duration_seconds=20.0,
            tokens_estimate=2000,
            success=True,
            mode="plan",
            files_changed=3,
            repair_attempts=1,
        ),
    ]

    report = render_benchmark_report("mock", results)

    assert report.profile == "mock"
    assert "loop" in report.modes
    assert "plan" in report.modes

    # Check loop summary
    loop_sum = report.summary["loop"]
    assert loop_sum["runs"] == 2.0
    assert loop_sum["success_rate"] == 0.5
    assert loop_sum["mean_turns"] == 2.0  # (3 + 1)/2
    assert loop_sum["mean_tokens"] == 750.0  # (1000 + 500)/2
    assert loop_sum["mean_duration"] == 7.5  # (10.0 + 5.0)/2
    assert loop_sum["total_files_changed"] == 2.0  # 2 + 0

    # Check plan summary
    plan_sum = report.summary["plan"]
    assert plan_sum["runs"] == 1.0
    assert plan_sum["success_rate"] == 1.0
    assert plan_sum["mean_turns"] == 4.0
    assert plan_sum["mean_tokens"] == 2000.0
    assert plan_sum["mean_duration"] == 20.0
    assert plan_sum["total_files_changed"] == 3.0

    # Check markdown content
    assert "loop" in report.markdown
    assert "plan" in report.markdown
    assert "## 按范式聚合" in report.markdown
    assert "## 逐任务明细" in report.markdown


def test_benchmark_cli_modes(tmp_path: Path, monkeypatch):
    # Setup mock env
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    # Mock cwd to tmp_path
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    runner = CliRunner()

    # 1. Run cli with modes & json
    result_json = runner.invoke(app, ["benchmark", "--profile", "mock", "--modes", "loop,plan", "--json"])
    assert result_json.exit_code == 0

    data = json.loads(result_json.output)
    assert data["profile"] == "mock"
    assert set(data["modes"]) == {"loop", "plan"}
    assert len(data["results"]) == 6

    # 2. Run cli with modes & non-json to write file
    result_text = runner.invoke(app, ["benchmark", "--profile", "mock", "--modes", "loop,plan"])
    assert result_text.exit_code == 0
    assert "Report written to .xhx/benchmark/report.md" in result_text.output

    report_md = tmp_path / ".xhx" / "benchmark" / "report.md"
    report_json = tmp_path / ".xhx" / "benchmark" / "report.json"
    assert report_md.exists()
    assert report_json.exists()

    md_content = report_md.read_text(encoding="utf-8")
    assert "三范式 Benchmark 对比" in md_content
    assert "loop" in md_content
    assert "plan" in md_content
