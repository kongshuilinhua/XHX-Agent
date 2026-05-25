from pathlib import Path
import json

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.context.debug import write_context_debug_report
from xhx_agent.evidence.store import EvidenceEntry
from xhx_agent.repo_intel.scanner import scan_project


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
