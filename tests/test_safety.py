from pathlib import Path

from xhx_agent.safety.checkpoint import create_checkpoint
from xhx_agent.safety.policy import decide_tool
from xhx_agent.safety.repair import MAX_REPAIR_ATTEMPTS, decide_repair
from xhx_agent.safety.risk import RiskLevel, classify_command


def test_command_risk_classification() -> None:
    assert classify_command("git status --short") is RiskLevel.SAFE
    assert classify_command("python -m pytest") is RiskLevel.CONFIRM
    assert classify_command("git reset --hard") is RiskLevel.DENY


def test_tool_policy_decisions() -> None:
    assert decide_tool("read_file").decision == "allow"
    assert decide_tool("search").risk is RiskLevel.SAFE
    assert decide_tool("apply_patch").risk is RiskLevel.CONFIRM
    assert decide_tool("terminal").decision == "deny"


def test_checkpoint_records_changed_file_hash(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")

    checkpoint = create_checkpoint(tmp_path, "run-test", ["demo.py"])

    assert checkpoint.id == "checkpoint-run-test"
    assert checkpoint.files[0].path == "demo.py"
    assert checkpoint.files[0].size_bytes > 0
    assert (tmp_path / ".xhx" / "checkpoints" / "run-test.json").exists()


def test_repair_decision_stops_when_disabled() -> None:
    decision = decide_repair("failed", attempts_used=0, auto_repair_enabled=False)
    assert not decision.should_repair
    assert decision.max_attempts == MAX_REPAIR_ATTEMPTS
    assert "not enabled" in decision.reason


def test_repair_decision_stops_at_limit() -> None:
    decision = decide_repair("failed", attempts_used=MAX_REPAIR_ATTEMPTS, auto_repair_enabled=True)
    assert not decision.should_repair
    assert "limit" in decision.reason
