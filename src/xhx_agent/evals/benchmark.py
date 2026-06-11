from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

# 三范式对比的默认范式集（协议都用 tool-calling，只差控制流）。
DEFAULT_BENCHMARK_MODES = ["loop", "plan", "graph"]


class BenchmarkFixture(BaseModel):
    id: str
    name: str
    task: str


class BenchmarkResult(BaseModel):
    fixture_id: str
    name: str
    profile: str
    status: str
    turns: int
    duration_seconds: float
    tokens_estimate: int
    success: bool
    mode: str = ""
    files_changed: int = 0
    repair_attempts: int = 0


class BenchmarkReport(BaseModel):
    """三范式对比报告：逐 (任务×范式) 明细 + 按范式聚合 + markdown 渲染。"""

    profile: str
    modes: list[str]
    results: list[BenchmarkResult]
    summary: dict[str, dict[str, float]]
    markdown: str


class BenchmarkRunner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.fixtures = [
            BenchmarkFixture(
                id="research-symbols",
                name="Research package symbols",
                task="Find where write_report is defined and how it works",
            ),
            BenchmarkFixture(
                id="config-diagnose", name="Config diagnostics", task="Scan the project config settings and profiles"
            ),
            BenchmarkFixture(
                id="skills-triggers",
                name="Verify skill trigger rules",
                task="Identify triggers for skill loaded from the system",
            ),
        ]

    def _run_fixture(self, app, fixture: BenchmarkFixture, profile_name: str, mode: str | None) -> BenchmarkResult:
        """跑一个 (fixture, mode)，把 RunResult 折成 BenchmarkResult；任何异常折成 failed 结果（基准不应崩）。"""
        start_time = time.time()
        try:
            res = app.run_task(fixture.task, profile_name=profile_name, assume_yes=True, mode=mode)
            metrics = res.metrics
            return BenchmarkResult(
                fixture_id=fixture.id,
                name=fixture.name,
                profile=profile_name,
                mode=mode or "",
                status=res.status,
                turns=res.turns,
                duration_seconds=metrics.duration_seconds if metrics else round(time.time() - start_time, 2),
                tokens_estimate=metrics.tokens_estimate if metrics else 0,
                files_changed=len(res.changed_files),
                repair_attempts=metrics.repair_attempts if metrics else 0,
                success=(res.status == "success"),
            )
        except Exception:
            return BenchmarkResult(
                fixture_id=fixture.id,
                name=fixture.name,
                profile=profile_name,
                mode=mode or "",
                status="failed",
                turns=0,
                duration_seconds=round(time.time() - start_time, 2),
                tokens_estimate=0,
                files_changed=0,
                repair_attempts=0,
                success=False,
            )

    def run_benchmark(self, profile_name: str) -> list[BenchmarkResult]:
        """单 profile、默认编排（向后兼容的原行为）。"""
        from xhx_agent.runtime.app import RuntimeApp

        app = RuntimeApp(workspace=self.workspace)
        return [self._run_fixture(app, fixture, profile_name, None) for fixture in self.fixtures]

    def run_matrix(self, profile_name: str, modes: list[str] | None = None) -> list[BenchmarkResult]:
        """范式矩阵：每个 fixture 分别用 modes 里的每种范式跑一遍。"""
        from xhx_agent.runtime.app import RuntimeApp

        modes = modes or DEFAULT_BENCHMARK_MODES
        app = RuntimeApp(workspace=self.workspace)
        results: list[BenchmarkResult] = []
        for fixture in self.fixtures:
            for mode in modes:
                results.append(self._run_fixture(app, fixture, profile_name, mode))
        return results


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def render_benchmark_report(profile: str, results: list[BenchmarkResult]) -> BenchmarkReport:
    """把矩阵结果聚合成按范式对比的报告（含 markdown 表）。"""
    modes = sorted({r.mode for r in results})
    summary: dict[str, dict[str, float]] = {}
    for mode in modes:
        rs = [r for r in results if r.mode == mode]
        n = len(rs)
        summary[mode] = {
            "runs": float(n),
            "success_rate": round(sum(1 for r in rs if r.success) / n, 3) if n else 0.0,
            "mean_turns": _mean([r.turns for r in rs]),
            "mean_tokens": _mean([float(r.tokens_estimate) for r in rs]),
            "mean_duration": _mean([r.duration_seconds for r in rs]),
            "total_files_changed": float(sum(r.files_changed for r in rs)),
        }

    lines: list[str] = [f"# 三范式 Benchmark 对比（profile: {profile}）", ""]
    lines.append("## 按范式聚合")
    lines.append("| 范式 | 任务数 | 成功率 | 平均轮数 | 平均 tokens | 平均耗时(s) | 改动文件数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for mode in modes:
        s = summary[mode]
        lines.append(
            f"| `{mode}` | {int(s['runs'])} | {s['success_rate']:.0%} | {s['mean_turns']} | "
            f"{s['mean_tokens']} | {s['mean_duration']} | {int(s['total_files_changed'])} |"
        )
    lines += ["", "## 逐任务明细", "| 任务 | 范式 | 状态 | 轮数 | tokens | 耗时(s) | 改动 |", "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r.fixture_id} | `{r.mode}` | {r.status} | {r.turns} | "
            f"{r.tokens_estimate} | {r.duration_seconds} | {r.files_changed} |"
        )
    return BenchmarkReport(profile=profile, modes=modes, results=results, summary=summary, markdown="\n".join(lines))
