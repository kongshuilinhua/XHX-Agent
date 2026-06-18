"""verification action 执行器测试：按改动文件推断并运行定向测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xhx_agent.hooks.executors import execute_verification
from xhx_agent.hooks.models import Action, HookContext


def _make_python_project(root: Path, test_body: str) -> None:
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_sample.py").write_text(test_body, encoding="utf-8")


def _run(work_dir: Path, changed: list[str]) -> tuple[bool, str]:
    ctx = HookContext(event_name="stop", work_dir=str(work_dir), changed_files=changed)
    action = Action(type="verification", timeout=120)
    result = asyncio.run(execute_verification(action, ctx))
    return result.success, result.output


def test_verification_passes_for_green_tests(tmp_path: Path) -> None:
    _make_python_project(tmp_path, "def test_ok():\n    assert 1 + 1 == 2\n")

    success, output = _run(tmp_path, ["tests/test_sample.py"])

    assert success is True
    assert "passed" in output.lower() or "Verification passed" in output


def test_verification_fails_for_red_tests(tmp_path: Path) -> None:
    _make_python_project(tmp_path, "def test_bad():\n    assert 1 + 1 == 3\n")

    success, output = _run(tmp_path, ["tests/test_sample.py"])

    assert success is False
    assert "FAILED" in output


def test_verification_skips_when_nothing_inferred(tmp_path: Path) -> None:
    # 空目录、无改动文件 → 推断不出命令 → 视为成功跳过。
    success, output = _run(tmp_path, [])

    assert success is True
