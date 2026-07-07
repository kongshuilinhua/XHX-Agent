"""Benchmark 台架：同一任务集 × 多个模型 profile 的量化对比。

早期的"三范式矩阵"（loop/plan/team 按 `--modes` 分流）已随旧编排器栈退役——统一
Agent 循环下不存在按 mode 分流的执行路径，按范式打标签只会产出三份相同的数字。
现在唯一的矩阵维度是**模型 profile**（如强模型 vs 便宜模型），对比成功率 / 轮数 /
token / 耗时，直接服务多模型路由（`config.routing`）的取舍决策。
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel


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
    files_changed: int = 0


class BenchmarkReport(BaseModel):
    """profile 对比报告：逐 (任务×profile) 明细 + 按 profile 聚合 + markdown 渲染。"""

    profiles: list[str]
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

    def _run_fixture(self, fixture: BenchmarkFixture, profile_name: str) -> BenchmarkResult:
        """跑一个 (fixture, profile)，把 HeadlessResult 折成 BenchmarkResult；任何异常折成 failed 结果。"""
        from xhx_agent.runtime.headless import run_headless_task

        start_time = time.time()
        try:
            res = run_headless_task(
                self.workspace,
                fixture.task,
                profile=profile_name,
                assume_yes=True,
                verify=True,
            )
            tokens_estimate = res.input_tokens + res.output_tokens
            files_changed = len(res.changed_files) if res.changed_files else 0
            return BenchmarkResult(
                fixture_id=fixture.id,
                name=fixture.name,
                profile=profile_name,
                status=res.status,
                turns=res.turns,
                duration_seconds=round(time.time() - start_time, 2),
                tokens_estimate=tokens_estimate,
                files_changed=files_changed,
                success=(res.status == "completed"),
            )
        except Exception:
            return BenchmarkResult(
                fixture_id=fixture.id,
                name=fixture.name,
                profile=profile_name,
                status="failed",
                turns=0,
                duration_seconds=round(time.time() - start_time, 2),
                tokens_estimate=0,
                files_changed=0,
                success=False,
            )

    def run_benchmark(self, profile_name: str) -> list[BenchmarkResult]:
        """单 profile：每个 fixture 跑一遍。"""
        return [self._run_fixture(fixture, profile_name) for fixture in self.fixtures]

    def run_matrix(self, profiles: list[str]) -> list[BenchmarkResult]:
        """profile 矩阵：每个 fixture 分别用每个 profile 跑一遍。"""
        results: list[BenchmarkResult] = []
        for fixture in self.fixtures:
            for profile_name in profiles:
                results.append(self._run_fixture(fixture, profile_name))
        return results


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def render_benchmark_report(results: list[BenchmarkResult]) -> BenchmarkReport:
    """把矩阵结果聚合成按 profile 对比的报告（含 markdown 表）。"""
    profiles = sorted({r.profile for r in results})
    summary: dict[str, dict[str, float]] = {}
    for profile_name in profiles:
        rs = [r for r in results if r.profile == profile_name]
        n = len(rs)
        summary[profile_name] = {
            "runs": float(n),
            "success_rate": round(sum(1 for r in rs if r.success) / n, 3) if n else 0.0,
            "mean_turns": _mean([r.turns for r in rs]),
            "mean_tokens": _mean([float(r.tokens_estimate) for r in rs]),
            "mean_duration": _mean([r.duration_seconds for r in rs]),
            "total_files_changed": float(sum(r.files_changed for r in rs)),
        }

    lines: list[str] = ["# 模型 Profile Benchmark 对比", ""]
    lines.append("## 按 profile 聚合")
    lines.append("| profile | 任务数 | 成功率 | 平均轮数 | 平均 tokens | 平均耗时(s) | 改动文件数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for profile_name in profiles:
        s = summary[profile_name]
        lines.append(
            f"| `{profile_name}` | {int(s['runs'])} | {s['success_rate']:.0%} | {s['mean_turns']} | "
            f"{s['mean_tokens']} | {s['mean_duration']} | {int(s['total_files_changed'])} |"
        )
    lines += [
        "",
        "## 逐任务明细",
        "| 任务 | profile | 状态 | 轮数 | tokens | 耗时(s) | 改动 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.fixture_id} | `{r.profile}` | {r.status} | {r.turns} | "
            f"{r.tokens_estimate} | {r.duration_seconds} | {r.files_changed} |"
        )
    return BenchmarkReport(profiles=profiles, results=results, summary=summary, markdown="\n".join(lines))
