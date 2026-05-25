from pathlib import Path

from xhx_agent.evidence.store import EvidenceStore


def test_evidence_store_reads_entries_and_expands_trace_ref(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path, "run-test")
    trace = store.write_trace("tool_call", {"tool": "read_file", "path": "README.md"})
    evidence = store.write_evidence(
        kind="file",
        source="README.md",
        summary="read README",
        artifact_ref=f"trace://{trace.id}",
    )

    assert store.list_traces()[0].id == trace.id
    assert store.list_evidence()[0].id == evidence.id
    assert store.get_evidence(evidence.id) == evidence
    expansion = store.expand_artifact_ref(evidence.artifact_ref)
    assert expansion.status == "found"
    assert expansion.payload["id"] == trace.id


def test_evidence_store_reports_missing_artifact_ref(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path, "run-test")

    expansion = store.expand_artifact_ref("trace://missing")

    assert expansion.status == "missing"
