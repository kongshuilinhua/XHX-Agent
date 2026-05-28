import json
from pathlib import Path

from xhx_agent.repo_intel.repo_map import build_repo_map
from xhx_agent.repo_intel.symbols import build_symbol_index, search_symbols
from xhx_agent.repo_intel.scanner import scan_project
from xhx_agent.repo_intel.xhx_md import render_xhx_md
from xhx_agent.repo_intel.context_builder import build_symbol_context
from xhx_agent.repo_intel.imports import build_import_graph, impacted_tests_from_imports
from xhx_agent.repo_intel.impact import analyze_impact
from xhx_agent.repo_intel.index import load_repo_intel_index, read_repo_intel_index, write_repo_intel_index
from xhx_agent.repo_intel.references import build_reference_index, search_references


def test_repo_map_classifies_files_and_verification_hints(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test.js","build":"node -c src/index.js"}}', encoding="utf-8")
    (tmp_path / ".xhx").mkdir()
    (tmp_path / ".xhx" / "ignored.py").write_text("def hidden(): pass\n", encoding="utf-8")

    repo_map = build_repo_map(tmp_path)

    files = {item.path: item for item in repo_map.files}
    assert files["src/calc.py"].language == "python"
    assert files["src/calc.py"].kind == "source"
    assert files["tests/test_calc.py"].kind == "test"
    assert "python -m pytest" in repo_map.verification_hints
    assert "npm test" in repo_map.verification_hints
    assert "npm run build" in repo_map.verification_hints
    assert ".xhx/ignored.py" not in files


def test_symbol_index_extracts_python_and_js_ts_symbols(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "\n".join(
            [
                "class Calculator:",
                "    def add(self, a, b):",
                "        return a + b",
                "",
                "async def load():",
                "    return Calculator()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "index.js").write_text(
        "\n".join(
            [
                "export function add(a, b) { return a + b; }",
                "const subtract = (a, b) => a - b;",
                "class Runner {}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "view.ts").write_text(
        "export const render = (name: string) => name;\n",
        encoding="utf-8",
    )

    index = build_symbol_index(tmp_path)
    by_key = {(symbol.path, symbol.name): symbol for symbol in index.symbols}

    assert by_key[("src/calc.py", "Calculator")].kind == "class"
    assert by_key[("src/calc.py", "add")].line == 2
    assert by_key[("src/calc.py", "add")].parent == "Calculator"
    assert by_key[("src/calc.py", "load")].kind == "function"
    assert by_key[("src/index.js", "add")].language == "javascript"
    assert by_key[("src/index.js", "subtract")].language == "javascript"
    assert by_key[("src/index.js", "Runner")].kind == "class"
    assert by_key[("src/view.ts", "render")].language == "typescript"


def test_search_symbols_prioritizes_exact_prefix_then_contains(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "items.py").write_text(
        "\n".join(
            [
                "def render():",
                "    pass",
                "def render_page():",
                "    pass",
                "def prerender():",
                "    pass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    index = build_symbol_index(tmp_path)

    results = search_symbols(index, "render")

    assert [symbol.name for symbol in results] == ["render", "render_page", "prerender"]


def test_reference_index_finds_symbol_usages_without_definition_lines(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")

    references = build_reference_index(tmp_path)
    results = search_references(references, "add")

    assert ("src/calc.py", 1) not in {(reference.path, reference.line) for reference in results}
    assert any(reference.path == "tests/test_calc.py" and reference.line == 1 for reference in results)
    assert any(reference.path == "tests/test_calc.py" and reference.line == 4 for reference in results)


def test_xhx_md_includes_repo_map_and_symbol_summary(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    pass\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    content = render_xhx_md(scan_project(tmp_path))

    assert "## Repo Map" in content
    assert "- by kind:" in content
    assert "- tests/test_calc.py" in content
    assert "## Symbols" in content
    assert "add (function, python) at src/calc.py:1" in content
    assert "test_add (function, python) at tests/test_calc.py:1" in content
    assert "- python -m pytest" in content


def test_context_builder_returns_symbol_excerpt(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "def add(a, b):",
                "    value = a + b",
                "    return value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    contexts = build_symbol_context(tmp_path, "add", context_lines=1)

    assert len(contexts) == 1
    assert contexts[0].symbol.name == "add"
    assert contexts[0].symbol.path == "src/calc.py"
    assert "4: def add(a, b):" in contexts[0].excerpt
    assert "5:     value = a + b" in contexts[0].excerpt


def test_impact_maps_python_source_to_direct_test(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    pass\n", encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/calc.py"])

    assert impact.impacted_tests == ["tests/test_calc.py"]
    assert impact.verification_hints[0] == "python -m pytest tests/test_calc.py"


def test_impact_maps_js_source_to_direct_test(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "src" / "index.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "test" / "index.test.js").write_text("import '../src/index.js';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test/index.test.js"}}', encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/index.js"])

    assert impact.impacted_tests == ["test/index.test.js"]
    assert "npm test" in impact.verification_hints
    assert any("Direct JS/TS tests were mapped" in note for note in impact.notes)


def test_impact_maps_ts_source_to_spec_test(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "view.ts").write_text("export const render = () => '';\n", encoding="utf-8")
    (tmp_path / "tests" / "view.spec.ts").write_text("import '../src/view';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest","typecheck":"tsc --noEmit"}}', encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/view.ts"])

    assert impact.impacted_tests == ["tests/view.spec.ts"]
    assert impact.verification_hints == ["npm test", "npm run typecheck"]


def test_import_graph_tracks_python_test_imports(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "math_ops.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from math_ops import add\n", encoding="utf-8")

    repo_map = build_repo_map(tmp_path)
    graph = build_import_graph(tmp_path, repo_map)

    assert any(edge.importer == "tests/test_public_api.py" and edge.target == "src/math_ops.py" for edge in graph.edges)
    assert impacted_tests_from_imports(graph, ["src/math_ops.py"], repo_map) == ["tests/test_public_api.py"]


def test_import_graph_maps_recursive_python_test_dependents(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "math_ops.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "src" / "public_api.py").write_text("from math_ops import add\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from public_api import add\n", encoding="utf-8")

    repo_map = build_repo_map(tmp_path)
    graph = build_import_graph(tmp_path, repo_map)

    assert impacted_tests_from_imports(graph, ["src/math_ops.py"], repo_map) == ["tests/test_public_api.py"]


def test_impact_uses_import_graph_when_direct_name_mapping_misses(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "math_ops.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from math_ops import add\n", encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/math_ops.py"])

    assert impact.impacted_tests == ["tests/test_public_api.py"]
    assert impact.verification_hints[0] == "python -m pytest tests/test_public_api.py"
    assert "Import graph mapped changed source files to dependent tests." in impact.notes


def test_impact_uses_recursive_import_graph_when_direct_name_mapping_misses(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "math_ops.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "src" / "public_api.py").write_text("from math_ops import add\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from public_api import add\n", encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/math_ops.py"])

    assert impact.impacted_tests == ["tests/test_public_api.py"]
    assert impact.verification_hints[0] == "python -m pytest tests/test_public_api.py"
    assert "Import graph mapped changed source files to dependent tests." in impact.notes


def test_impact_uses_js_import_graph_when_direct_name_mapping_misses(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "src" / "math_ops.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "test" / "public-api.test.js").write_text("import { add } from '../src/math_ops.js';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test/public-api.test.js"}}', encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/math_ops.js"])

    assert impact.impacted_tests == ["test/public-api.test.js"]
    assert "npm test" in impact.verification_hints
    assert "Import graph mapped changed source files to dependent tests." in impact.notes


def test_impact_uses_recursive_js_import_graph_when_direct_name_mapping_misses(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "src" / "math_ops.js").write_text("export const add = (a, b) => a + b;\n", encoding="utf-8")
    (tmp_path / "src" / "public_api.js").write_text("import { add } from './math_ops.js';\nexport { add };\n", encoding="utf-8")
    (tmp_path / "test" / "public-api.test.js").write_text("import { add } from '../src/public_api.js';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node test/public-api.test.js"}}', encoding="utf-8")

    impact = analyze_impact(tmp_path, ["src/math_ops.js"])

    assert impact.impacted_tests == ["test/public-api.test.js"]
    assert "npm test" in impact.verification_hints
    assert "Import graph mapped changed source files to dependent tests." in impact.notes


def test_repo_intel_index_round_trips_to_json(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n", encoding="utf-8")

    path = write_repo_intel_index(tmp_path)
    index = read_repo_intel_index(tmp_path)

    assert path == tmp_path / ".xhx" / "repo" / "index.json"
    assert index.schema_version == 1
    assert index.content_fingerprint
    assert any(item.path == "src/calc.py" for item in index.repo_map.files)
    assert any(symbol.name == "add" for symbol in index.symbol_index.symbols)
    assert any(edge.importer == "tests/test_calc.py" and edge.target == "src/calc.py" for edge in index.import_graph.edges)
    assert any(reference.name == "add" and reference.path == "tests/test_calc.py" for reference in index.reference_index.references)


def test_load_repo_intel_index_rebuilds_when_file_content_changes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "calc.py"
    source.write_text("def old_name(a, b):\n    return a + b\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)
    stored = read_repo_intel_index(tmp_path)

    source.write_text("def new_name(a, b):\n    return a + b\n# changed\n", encoding="utf-8")
    loaded = load_repo_intel_index(tmp_path)

    assert loaded.content_fingerprint != stored.content_fingerprint
    assert any(symbol.name == "new_name" for symbol in loaded.symbol_index.symbols)
    assert not any(symbol.name == "old_name" for symbol in loaded.symbol_index.symbols)


def test_load_repo_intel_index_rebuilds_when_file_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "calc.py"
    source.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)

    source.unlink()
    loaded = load_repo_intel_index(tmp_path)

    assert not any(item.path == "src/calc.py" for item in loaded.repo_map.files)
    assert not any(symbol.path == "src/calc.py" for symbol in loaded.symbol_index.symbols)


def test_load_repo_intel_index_rebuilds_legacy_index_without_references(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    path = write_repo_intel_index(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("reference_index")
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_repo_intel_index(tmp_path)

    assert loaded.reference_index.root
    assert any(reference.name == "add" for reference in loaded.reference_index.references)
