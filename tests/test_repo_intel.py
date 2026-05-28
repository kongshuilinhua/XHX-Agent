from pathlib import Path

from xhx_agent.repo_intel.repo_map import build_repo_map
from xhx_agent.repo_intel.symbols import build_symbol_index, search_symbols
from xhx_agent.repo_intel.scanner import scan_project
from xhx_agent.repo_intel.xhx_md import render_xhx_md
from xhx_agent.repo_intel.context_builder import build_symbol_context
from xhx_agent.repo_intel.impact import analyze_impact


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
