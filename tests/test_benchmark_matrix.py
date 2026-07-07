import json
from pathlib import Path

from typer.testing import CliRunner

from xhx_agent.cli.main import app
from xhx_agent.evals.benchmark import BenchmarkResult, BenchmarkRunner, render_benchmark_report
from xhx_agent.runtime.config import write_default_config
from xhx_agent.runtime.profiles import write_default_profiles


def test_run_matrix_scale(tmp_path: Path):
    # profile 矩阵：每个 fixture × 每个 profile 跑一遍。
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    runner = BenchmarkRunner(tmp_path)

    results = runner.run_matrix(["mock", "mock"])
    # 3 fixtures * 2 profiles = 6 results
    assert len(results) == 6
    for r in results:
        assert r.profile == "mock"
        assert r.success is True


def test_render_benchmark_report_aggregation():
    # 聚合正确性：runs / success_rate / 各均值按 profile 分组。
    results = [
        BenchmarkResult(
            fixture_id="f1",
            name="fixture 1",
            profile="default",
            status="completed",
            turns=3,
            duration_seconds=10.0,
            tokens_estimate=1000,
            success=True,
            files_changed=2,
        ),
        BenchmarkResult(
            fixture_id="f2",
            name="fixture 2",
            profile="default",
            status="failed",
            turns=1,
            duration_seconds=5.0,
            tokens_estimate=500,
            success=False,
            files_changed=0,
        ),
        BenchmarkResult(
            fixture_id="f3",
            name="fixture 3",
            profile="cheap",
            status="completed",
            turns=4,
            duration_seconds=20.0,
            tokens_estimate=2000,
            success=True,
            files_changed=3,
        ),
    ]

    report = render_benchmark_report(results)

    assert set(report.profiles) == {"default", "cheap"}

    default_sum = report.summary["default"]
    assert default_sum["runs"] == 2.0
    assert default_sum["success_rate"] == 0.5
    assert default_sum["mean_turns"] == 2.0  # (3 + 1)/2
    assert default_sum["mean_tokens"] == 750.0  # (1000 + 500)/2
    assert default_sum["mean_duration"] == 7.5  # (10.0 + 5.0)/2
    assert default_sum["total_files_changed"] == 2.0  # 2 + 0

    cheap_sum = report.summary["cheap"]
    assert cheap_sum["runs"] == 1.0
    assert cheap_sum["success_rate"] == 1.0
    assert cheap_sum["mean_turns"] == 4.0
    assert cheap_sum["mean_tokens"] == 2000.0
    assert cheap_sum["mean_duration"] == 20.0
    assert cheap_sum["total_files_changed"] == 3.0

    assert "default" in report.markdown
    assert "cheap" in report.markdown
    assert "## 按 profile 聚合" in report.markdown
    assert "## 逐任务明细" in report.markdown


def test_benchmark_cli_profiles(tmp_path: Path, monkeypatch):
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    runner = CliRunner()

    # 1. --profiles + --json：结构化矩阵报告
    result_json = runner.invoke(app, ["benchmark", "--profiles", "mock", "--json"])
    assert result_json.exit_code == 0

    data = json.loads(result_json.output)
    assert data["profiles"] == ["mock"]
    assert len(data["results"]) == 3

    # 2. --profiles 非 json：写报告文件
    result_text = runner.invoke(app, ["benchmark", "--profiles", "mock"])
    assert result_text.exit_code == 0
    assert "Report written to .xhx/benchmark/report.md" in result_text.output

    report_md = tmp_path / ".xhx" / "benchmark" / "report.md"
    report_json = tmp_path / ".xhx" / "benchmark" / "report.json"
    assert report_md.exists()
    assert report_json.exists()

    md_content = report_md.read_text(encoding="utf-8")
    assert "模型 Profile Benchmark 对比" in md_content
    assert "mock" in md_content
