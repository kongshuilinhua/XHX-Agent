from xhx_agent.safety.risk import RiskLevel, classify_command


def test_command_risk_classification() -> None:
    assert classify_command("git status --short") is RiskLevel.SAFE
    assert classify_command("python -m pytest") is RiskLevel.CONFIRM
    assert classify_command("git reset --hard") is RiskLevel.DENY
