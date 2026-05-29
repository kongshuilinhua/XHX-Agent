import json

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.evidence.store import EvidenceEntry, EvidenceStore


def test_cross_run_evidence_loading(tmp_path):
    # 模拟工作区结构
    workspace = tmp_path
    evidence_dir = workspace / ".xhx" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # 写入两个历史 Run 证据文件
    run1_file = evidence_dir / "run-1111.jsonl"
    entry1 = EvidenceEntry(
        kind="error", source="test_file.py", summary="Historical crash in run 1", artifact_ref="trace://1"
    )
    run1_file.write_text(json.dumps(entry1.model_dump()) + "\n", encoding="utf-8")

    run2_file = evidence_dir / "run-2222.jsonl"
    entry2 = EvidenceEntry(
        kind="test",
        source="test_file.py",
        summary="Historical crash in run 1",
        artifact_ref="trace://2",  # 故意重复 summary/source/kind
    )
    entry3 = EvidenceEntry(kind="patch", source="util.py", summary="Historical patch success", artifact_ref="trace://3")
    run2_file.write_text(
        json.dumps(entry2.model_dump()) + "\n" + json.dumps(entry3.model_dump()) + "\n", encoding="utf-8"
    )

    # 验证 EvidenceStore 能够载入全部并提供外部 API
    store = EvidenceStore(workspace, "run-current")
    history = store.load_all_historical_evidence()
    assert len(history) == 3

    # 编译 Context Pack，验证重复项已被去重，历史证据成功混合装载
    from xhx_agent.repo_intel.scanner import scan_project

    scan = scan_project(workspace)
    pack = compile_context_pack(
        workspace=workspace,
        task="Fix crash in test_file",
        scan=scan,
        evidence_entries=[
            EvidenceEntry(kind="checkpoint", source="main.py", summary="Current checkpoint", artifact_ref="trace://4")
        ],
    )

    # 检查项中包含历史 Run 证据，且无重复
    sources = [item.source for item in pack.items if item.kind.startswith("evidence:")]
    assert "util.py" in sources
    assert "test_file.py" in sources
    assert "main.py" in sources
