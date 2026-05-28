from pathlib import Path

from xhx_agent.repo_intel.index import write_repo_intel_index
from xhx_agent.verification.router import infer_verification


def test_python_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["python -m pytest"]


def test_python_uv_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["python -m pytest"]


def test_python_test_file_verification_targets_changed_test(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    plan = infer_verification(tmp_path, changed_files=["tests/test_calc.py"])
    assert [command.command for command in plan.commands] == ["python -m pytest tests/test_calc.py"]


def test_python_source_change_uses_repo_intelligence_direct_test_mapping(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    pass\n", encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/calc.py"])

    assert [command.command for command in plan.commands] == ["python -m pytest tests/test_calc.py"]
    assert plan.commands[0].reason == "Repo intelligence mapped changed source files to direct tests."


def test_python_source_change_uses_persisted_import_graph_for_test_mapping(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from calc import add\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)

    def fail_graph_rebuild(*args, **kwargs):
        raise AssertionError("Verification Router should use the persisted import graph before rebuilding it.")

    monkeypatch.setattr("xhx_agent.repo_intel.impact.build_import_graph", fail_graph_rebuild)

    plan = infer_verification(tmp_path, changed_files=["src/calc.py"])

    assert [command.command for command in plan.commands] == ["python -m pytest tests/test_public_api.py"]


def test_node_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["npm test"]


def test_node_source_change_uses_repo_intelligence_reason(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "test" / "index.test.js").write_text("import '../src/index.js';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test/index.test.js","build":"node -c src/index.js"}}', encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/index.js"])

    assert [command.command for command in plan.commands] == ["npm test"]
    assert plan.commands[0].reason == "Repo intelligence mapped changed source files to direct JS/TS tests; package.json defines test script."


def test_node_build_fallback_without_test_script(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"build":"node -c src/index.js"}}', encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/index.js"])

    assert [command.command for command in plan.commands] == ["npm run build"]
