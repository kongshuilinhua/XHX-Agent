from pathlib import Path

from xhx_agent.verification.router import infer_verification


def test_python_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["python -m pytest"]


def test_python_uv_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["uv run pytest"]


def test_node_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["npm test"]
