import shutil
from pathlib import Path

from xhx_agent.orchestrators.registry import select_orchestrator
from xhx_agent.runtime.app import RuntimeApp

_FIX_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: src/calc.py\n"
    "@@\n"
    "-    return a - b  # TODO_BUG\n"
    "+    return a + b\n"
    "*** End Patch\n"
)
_STILL_WRONG_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: src/calc.py\n"
    "@@\n"
    "-    return a - b  # TODO_BUG\n"
    "+    return a * b\n"
    "*** End Patch\n"
)
_FIX_FROM_WRONG_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: src/calc.py\n"
    "@@\n"
    "-    return a * b\n"
    "+    return a + b\n"
    "*** End Patch\n"
)


def _python_bug_workspace(tmp_path: Path) -> Path:
    """复制 python_bug fixture（src/calc.py 含 a-b bug，tests/test_calc.py 断言 add(2,3)==5）。"""
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()
    return workspace


def test_plan_registry() -> None:
    assert select_orchestrator("plan").name == "plan"


def test_plan_conversational_no_changes(tmp_path, monkeypatch) -> None:
    import xhx_agent.orchestrators.plan as planmod
    from xhx_agent.models.types import ChatResult

    class _Fake:
        def chat(self, messages, tools):
            return ChatResult(content="Nothing to change; here is my analysis.", tool_calls=[])

    monkeypatch.setattr(planmod, "build_chat_client", lambda profile: _Fake())
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("explain this repo", profile_name="mock", mode="plan")

    assert res.status == "success"
    assert res.mode == "plan"
    assert res.answer
    assert res.changed_files == []
    # No changes -> verification is a skip class, not a real pass/fail.
    assert res.verification in {"skipped_no_changes", "not_executed"}


def _fake_chat_factory(monkeypatch, seq):
    import xhx_agent.orchestrators.plan as planmod
    from xhx_agent.models.types import ChatResult, ToolCall

    def _make(patch_or_text):
        if patch_or_text is None:
            return ChatResult(content="Done; the fix is applied and tests pass.", tool_calls=[])
        return ChatResult(
            content=None,
            tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch_or_text})],
        )

    results = [_make(item) for item in seq]

    class _Fake:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            r = results[min(self.i, len(results) - 1)]
            self.i += 1
            return r

    monkeypatch.setattr(planmod, "build_chat_client", lambda profile: _Fake())


def test_plan_runs_verification_after_changes(tmp_path, monkeypatch) -> None:
    workspace = _python_bug_workspace(tmp_path)
    # fake：先 apply_patch 修好 bug（a-b -> a+b），再回纯文本 done。
    _fake_chat_factory(monkeypatch, [_FIX_PATCH, None])

    res = RuntimeApp(workspace).run_task("fix the failing test", profile_name="mock", mode="plan", assume_yes=True)

    assert res.status == "success"
    assert "src/calc.py" in res.changed_files
    # 改动后必须真的跑了验证并通过（assume_yes 让 CONFIRM 档 pytest 真跑）。
    assert res.verification == "passed"
    assert any(r.status == "success" for r in res.verification_results)
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")


def test_plan_repair_fed_back_on_failure(tmp_path, monkeypatch) -> None:
    workspace = _python_bug_workspace(tmp_path)
    # 第一轮改成仍错（a*b：add(2,3)==6≠5）→ 验证失败 → 回喂 → 第二轮改成 a+b → 通过。
    _fake_chat_factory(monkeypatch, [_STILL_WRONG_PATCH, None, _FIX_FROM_WRONG_PATCH, None])

    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True, auto_repair=True
    )

    assert res.status == "success"
    assert res.verification == "passed"
    assert res.repair_attempts >= 1
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")


def test_plan_no_repair_when_disabled_keeps_failure(tmp_path, monkeypatch) -> None:
    workspace = _python_bug_workspace(tmp_path)
    # 改成仍错且 auto_repair=False：验证被调用、失败、不修复、不崩。
    _fake_chat_factory(monkeypatch, [_STILL_WRONG_PATCH, None])

    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True, auto_repair=False
    )

    assert "src/calc.py" in res.changed_files
    # 验证确实被调用并判为 failed；未修复（attempts 0）。
    assert res.verification == "failed"
    assert res.repair_attempts == 0
    assert any(r.status == "failed" for r in res.verification_results)


def test_plan_writes_patch_evidence_and_binding(tmp_path, monkeypatch):
    import json
    workspace = _python_bug_workspace(tmp_path)  # 复用 3a helper
    _fake_chat_factory(monkeypatch, [_FIX_PATCH, None])  # 复用 3a：apply_patch 修好 -> done
    res = RuntimeApp(workspace).run_task("fix the failing test", profile_name="mock", mode="plan", assume_yes=True)
    assert res.status == "success" and "src/calc.py" in res.changed_files
    ev_files = list((workspace / ".xhx" / "evidence").glob("*.jsonl"))
    tr_files = list((workspace / ".xhx" / "traces").glob("*.jsonl"))
    assert ev_files and tr_files
    evidence = [json.loads(line) for line in ev_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    traces = [json.loads(line) for line in tr_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    patch_ev = next(e for e in evidence if e["kind"] == "patch")
    binding = next(t for t in traces if t["type"] == "patch_evidence_binding")
    assert binding["payload"]["evidence_id"] == patch_ev["id"]
    assert binding["payload"]["changed_files"] == ["src/calc.py"]


def test_plan_creates_checkpoint_and_restore_on_failure(tmp_path, monkeypatch):
    workspace = _python_bug_workspace(tmp_path)
    # 改成仍错（a*b）且不开 auto_repair：验证失败 -> 应生成 checkpoint + restore plan。
    _fake_chat_factory(monkeypatch, [_STILL_WRONG_PATCH, None])
    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True, auto_repair=False
    )
    assert res.verification == "failed"
    assert res.checkpoint_path is not None and (workspace / res.checkpoint_path).exists()
    assert res.restore_plan_path is not None and (workspace / res.restore_plan_path).exists()


def test_plan_checkpoint_on_success(tmp_path, monkeypatch):
    workspace = _python_bug_workspace(tmp_path)
    _fake_chat_factory(monkeypatch, [_FIX_PATCH, None])
    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True
    )
    assert res.verification == "passed"
    assert res.checkpoint_path is not None and (workspace / res.checkpoint_path).exists()
    assert res.restore_plan_path is None  # 成功不生成 restore plan


def test_plan_with_fenced_unified_diff_patch(tmp_path, monkeypatch):
    workspace = _python_bug_workspace(tmp_path)
    # unified diff wrapped in ```diff fence
    unified_patch = (
        "```diff\n"
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def add(a: int, b: int) -> int:\n"
        "-    return a - b  # TODO_BUG\n"
        "+    return a + b\n"
        "```"
    )
    _fake_chat_factory(monkeypatch, [unified_patch, None])
    res = RuntimeApp(workspace).run_task(
        "fix the failing test", profile_name="mock", mode="plan", assume_yes=True
    )
    assert res.status == "success"
    assert "src/calc.py" in res.changed_files
    assert res.verification == "passed"
    assert (workspace / "src" / "calc.py").read_text(encoding="utf-8") == "def add(a: int, b: int) -> int:\n    return a + b\n"

