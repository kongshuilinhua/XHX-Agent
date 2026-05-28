from pathlib import Path
import json

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.context.debug import write_context_debug_report
from xhx_agent.evidence.store import EvidenceEntry
from xhx_agent.repo_intel.index import write_repo_intel_index
from xhx_agent.repo_intel.scanner import scan_project
from xhx_agent.repo_intel.symbols import build_symbol_index


def test_context_pack_includes_project_map_and_tool_summaries(tmp_path: Path) -> None:
    (tmp_path / "XHX.md").write_text("# Project Map\n\nRun pytest.\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("value = 1\n", encoding="utf-8")
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="fix bug",
        scan=scan,
        changed_files=["src/demo.py"],
        tool_summaries=["read_file: success: read src/demo.py"],
        evidence_entries=[
            EvidenceEntry(
                kind="file",
                source="src/demo.py",
                summary="read src/demo.py",
                artifact_ref="trace://demo",
            )
        ],
        budget_tokens=2_000,
    )

    assert pack.task == "fix bug"
    assert pack.used_tokens_estimate <= pack.budget_tokens
    assert any(item.kind == "project_map" for item in pack.items)
    assert any(item.kind == "changed_file" and item.source == "src/demo.py" for item in pack.items)
    assert any(item.kind == "tool_results" for item in pack.items)
    assert "Do not place the full Raw Trace in the model context." in pack.constraints
    assert pack.debug is not None
    assert pack.debug.selected_count == len(pack.items)
    assert any(record.selected for record in pack.debug.records)


def test_context_pack_includes_symbol_context_for_task_query(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "def add_numbers(a, b):",
                "    value = a + b",
                "    return value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="fix add_numbers bug",
        scan=scan,
        budget_tokens=2_000,
    )

    symbol_items = [item for item in pack.items if item.kind == "symbol_context"]
    assert len(symbol_items) == 1
    assert symbol_items[0].source == "src/calc.py:4:add_numbers"
    assert "4: def add_numbers(a, b):" in symbol_items[0].content
    assert "5:     value = a + b" in symbol_items[0].content
    assert pack.debug is not None
    assert any(record.kind == "symbol_context" and record.selected for record in pack.debug.records)


def test_context_pack_includes_import_context_for_changed_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "src" / "public_api.py").write_text("from calc import add\n\ndef add_public(a, b):\n    return add(a, b)\n", encoding="utf-8")
    (tmp_path / "tests" / "test_public_api.py").write_text("from public_api import add_public\n\ndef test_public_add():\n    assert add_public(1, 2) == 3\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="continue fixing behavior",
        scan=scan,
        changed_files=["src/calc.py"],
        budget_tokens=2_000,
    )

    import_items = [item for item in pack.items if item.kind == "import_context"]
    assert any(item.source == "src/public_api.py:3:add_public" for item in import_items)
    assert any("def add_public" in item.content for item in import_items)
    assert pack.debug is not None
    assert any(record.kind == "import_context" and record.selected for record in pack.debug.records)


def test_context_pack_uses_recent_error_path_for_import_context(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="repair failing tests",
        scan=scan,
        recent_error="tests/test_calc.py failed after editing src/calc.py",
        budget_tokens=2_000,
    )

    assert any(item.kind == "import_context" and item.source == "tests/test_calc.py:3:test_add" for item in pack.items)


def test_context_pack_keeps_recent_error_context_when_changed_files_exist(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="repair failing tests",
        scan=scan,
        changed_files=["./src/calc.py"],
        recent_error="FAILED tests/test_calc.py::test_add",
        budget_tokens=2_000,
    )

    assert any(item.kind == "import_context" and item.source == "tests/test_calc.py:3:test_add" for item in pack.items)


def test_context_pack_prefers_persisted_repo_index_for_symbol_context(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "calc.py"
    source.write_text("def cached_symbol(a, b):\n    return a + b\n", encoding="utf-8")
    write_repo_intel_index(tmp_path)
    scan = scan_project(tmp_path)

    def fail_immediate_scan(*args, **kwargs):
        raise AssertionError("Context Pack should use the persisted repo index before rebuilding symbols.")

    monkeypatch.setattr("xhx_agent.repo_intel.index.build_repo_intel_index", fail_immediate_scan)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="fix cached_symbol bug",
        scan=scan,
        budget_tokens=2_000,
    )

    symbol_items = [item for item in pack.items if item.kind == "symbol_context"]
    assert len(symbol_items) == 1
    assert symbol_items[0].source == "src/calc.py:1:cached_symbol"
    assert "1: def cached_symbol(a, b):" in symbol_items[0].content


def test_context_pack_falls_back_when_persisted_repo_index_is_invalid(tmp_path: Path) -> None:
    (tmp_path / ".xhx" / "repo").mkdir(parents=True)
    (tmp_path / ".xhx" / "repo" / "index.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n", encoding="utf-8")
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="fix add_numbers bug",
        scan=scan,
        budget_tokens=2_000,
    )

    assert any(item.kind == "symbol_context" and item.source == "src/calc.py:1:add_numbers" for item in pack.items)


def test_context_pack_fallback_rebuilds_symbols_for_invalid_repo_index(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".xhx" / "repo").mkdir(parents=True)
    (tmp_path / ".xhx" / "repo" / "index.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n", encoding="utf-8")
    scan = scan_project(tmp_path)
    called = {"value": False}

    def tracked_build(workspace, repo_map=None):
        called["value"] = True
        return build_symbol_index(workspace, repo_map)

    monkeypatch.setattr("xhx_agent.repo_intel.index.build_symbol_index", tracked_build)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="fix add_numbers bug",
        scan=scan,
        budget_tokens=2_000,
    )

    assert called["value"]
    assert any(item.kind == "symbol_context" and item.source == "src/calc.py:1:add_numbers" for item in pack.items)


def test_context_pack_omits_symbol_context_when_over_budget(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n", encoding="utf-8")
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="fix add_numbers bug",
        scan=scan,
        budget_tokens=30,
    )

    assert "symbol_context:src/calc.py:1:add_numbers" in pack.omitted
    assert pack.debug is not None
    assert any(record.kind == "symbol_context" and not record.selected for record in pack.debug.records)


def test_context_pack_omits_low_priority_items_when_over_budget(tmp_path: Path) -> None:
    (tmp_path / "XHX.md").write_text("x" * 4_000, encoding="utf-8")
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="analyze",
        scan=scan,
        tool_summaries=["summary " * 1_000],
        budget_tokens=100,
    )

    assert pack.omitted
    assert pack.used_tokens_estimate <= pack.budget_tokens
    assert pack.debug is not None
    assert pack.debug.omitted_count == len(pack.omitted)


def test_context_pack_selects_top_k_evidence_by_priority(tmp_path: Path) -> None:
    scan = scan_project(tmp_path)
    entries = [
        EvidenceEntry(kind="policy", source=f"policy-{index}", summary="low", artifact_ref="trace://low", confidence=0.2)
        for index in range(6)
    ]
    entries.append(
        EvidenceEntry(kind="test", source="pytest", summary="high value failure", artifact_ref="trace://test", confidence=0.95)
    )

    pack = compile_context_pack(
        workspace=tmp_path,
        task="repair failing tests",
        scan=scan,
        evidence_entries=entries,
        top_k_evidence=3,
        budget_tokens=2_000,
    )

    evidence_items = [item for item in pack.items if item.kind.startswith("evidence:")]
    assert len(evidence_items) == 3
    assert any(item.source == "pytest" for item in evidence_items)


def test_context_pack_limits_changed_files_and_records_omissions(tmp_path: Path) -> None:
    for index in range(12):
        path = tmp_path / f"file_{index}.py"
        path.write_text(f"value = {index}\n", encoding="utf-8")
    scan = scan_project(tmp_path)

    pack = compile_context_pack(
        workspace=tmp_path,
        task="continue edit",
        scan=scan,
        changed_files=[f"file_{index}.py" for index in range(12)],
        budget_tokens=4_000,
    )

    changed_items = [item for item in pack.items if item.kind == "changed_file"]
    assert len(changed_items) == 8
    assert "changed_file:file_0.py" in pack.omitted
    assert pack.debug is not None
    assert any(record.source == "file_0.py" and not record.selected for record in pack.debug.records)


def test_context_debug_report_is_written(tmp_path: Path) -> None:
    scan = scan_project(tmp_path)
    pack = compile_context_pack(workspace=tmp_path, task="analyze", scan=scan)

    path = write_context_debug_report(tmp_path, "run-test", 1, pack)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-test"
    assert data["turn"] == 1
    assert data["debug"]["budget_tokens"] == pack.budget_tokens
