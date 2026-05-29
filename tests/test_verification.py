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


def test_python_config_change_runs_pytest_when_tests_exist(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["pyproject.toml"])

    assert [command.command for command in plan.commands] == ["python -m pytest"]


def test_python_source_change_uses_repo_intelligence_direct_test_mapping(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    pass\n", encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/calc.py"])

    assert [command.command for command in plan.commands] == ["python -m pytest tests/test_calc.py"]
    assert plan.commands[0].reason == "Repo intelligence mapped changed source files to dependent tests."


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


def test_python_source_change_uses_recursive_import_graph_for_targeted_pytest(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "src" / "public_api.py").write_text("from calc import add\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from public_api import add\n", encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/calc.py"])

    assert [command.command for command in plan.commands] == ["python -m pytest tests/test_public_api.py"]
    assert plan.commands[0].reason == "Repo intelligence mapped changed source files to dependent tests."


def test_node_verification_inference(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    plan = infer_verification(tmp_path)
    assert [command.command for command in plan.commands] == ["npm test"]


def test_node_source_change_uses_targeted_vitest_when_supported(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "test" / "index.test.js").write_text("import '../src/index.js';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run","build":"node -c src/index.js"}}', encoding="utf-8"
    )

    plan = infer_verification(tmp_path, changed_files=["src/index.js"])

    assert [command.command for command in plan.commands] == ["npm test -- test/index.test.js"]
    assert plan.commands[0].reason == "Repo intelligence mapped changed source files to a targeted JS/TS test command."


def test_node_source_change_keeps_portable_npm_test_for_unknown_runner(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "test" / "index.test.js").write_text("import '../src/index.js';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node test/index.test.js","build":"node -c src/index.js"}}', encoding="utf-8"
    )

    plan = infer_verification(tmp_path, changed_files=["src/index.js"])

    assert [command.command for command in plan.commands] == ["npm test"]
    assert (
        plan.commands[0].reason
        == "Repo intelligence mapped changed source files to dependent JS/TS tests; package.json defines test script."
    )


def test_node_source_change_uses_targeted_jest_for_import_graph_mapping(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "math_ops.ts").write_text(
        "export const add = (a: number, b: number) => a + b;\n", encoding="utf-8"
    )
    (tmp_path / "src" / "public_api.ts").write_text(
        "import { add } from './math_ops';\nexport { add };\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "public_api.spec.ts").write_text(
        "import { add } from '../src/public_api';\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text('{"scripts":{"test":"jest --runInBand"}}', encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/math_ops.ts"])

    assert [command.command for command in plan.commands] == ["npm test -- tests/public_api.spec.ts"]


def test_node_project_with_tests_directory_does_not_route_ts_tests_to_pytest(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "view.ts").write_text("export const render = () => '';\n", encoding="utf-8")
    (tmp_path / "tests" / "view.spec.ts").write_text("import '../src/view';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/view.ts"])

    assert [command.command for command in plan.commands] == ["npm test -- tests/view.spec.ts"]


def test_node_verification_ignores_invalid_package_json(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{not-json", encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/index.js"])

    assert plan.commands == []
    assert plan.skip_reason == "No safe verification command inferred."


def test_node_build_fallback_without_test_script(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"build":"node -c src/index.js"}}', encoding="utf-8")

    plan = infer_verification(tmp_path, changed_files=["src/index.js"])

    assert [command.command for command in plan.commands] == ["npm run build"]
