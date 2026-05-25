from pathlib import Path

from xhx_agent.context.compiler import compile_context_pack
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
