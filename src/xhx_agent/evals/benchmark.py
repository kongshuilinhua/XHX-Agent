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

    def run_benchmark(self, profile_name: str) -> list[BenchmarkResult]:
        from xhx_agent.runtime.app import RuntimeApp

        results: list[BenchmarkResult] = []
        app = RuntimeApp(workspace=self.workspace)

        for fixture in self.fixtures:
            start_time = time.time()
            try:
                res = app.run_task(fixture.task, profile_name=profile_name, assume_yes=True)
                metrics = res.metrics

                results.append(
                    BenchmarkResult(
                        fixture_id=fixture.id,
                        name=fixture.name,
                        profile=profile_name,
                        status=res.status,
                        turns=res.turns,
                        duration_seconds=metrics.duration_seconds if metrics else round(time.time() - start_time, 2),
                        tokens_estimate=metrics.tokens_estimate if metrics else 0,
                        success=(res.status == "success"),
                    )
                )
            except Exception:
                results.append(
                    BenchmarkResult(
                        fixture_id=fixture.id,
                        name=fixture.name,
                        profile=profile_name,
                        status="failed",
                        turns=0,
                        duration_seconds=round(time.time() - start_time, 2),
                        tokens_estimate=0,
                        success=False,
                    )
                )
        return results
