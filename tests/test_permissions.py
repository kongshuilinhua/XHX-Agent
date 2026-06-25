"""PermissionChecker integration tests — replaces old path-scope tests."""

from pathlib import Path

from xhx_agent.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)


def test_path_sandbox_denies_out_of_scope(tmp_path: Path) -> None:
    """PermissionChecker Layer 2 blocks paths outside workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 盘符根下、既不在 workspace 也不在系统临时目录内的绝对路径——跨平台都"越界"。
    # （写死 C:/... 在 Linux 上是相对路径会落进 workspace；用 tmp_path 又会命中沙箱自动放行的临时目录。）
    ext_file = Path(tmp_path.anchor) / "xhx_outside_sandbox" / "README.md"

    sandbox = PathSandbox(workspace)
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=sandbox,
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )

    decision = checker.check("read_file", {"path": str(ext_file)})
    assert decision.effect == "deny"


def test_path_sandbox_allows_in_scope(tmp_path: Path) -> None:
    """In-scope paths pass through PermissionChecker."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    in_file = workspace / "in.md"
    in_file.write_text("content\n", encoding="utf-8")

    sandbox = PathSandbox(workspace)
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=sandbox,
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )

    decision = checker.check("read_file", {"path": str(in_file)})
    # 只读工具在默认模式下应放行
    assert decision.effect == "allow"


def test_path_sandbox_bypass_allows_out_of_scope(tmp_path: Path) -> None:
    """Bypass permission mode grants out-of-scope access."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 盘符根下、既不在 workspace 也不在系统临时目录内的绝对路径——跨平台都"越界"。
    # （写死 C:/... 在 Linux 上是相对路径会落进 workspace；用 tmp_path 又会命中沙箱自动放行的临时目录。）
    ext_file = Path(tmp_path.anchor) / "xhx_outside_sandbox" / "README.md"

    sandbox = PathSandbox(workspace)
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=sandbox,
        rule_engine=RuleEngine(),
        mode=PermissionMode.BYPASS,
    )

    decision = checker.check("read_file", {"path": str(ext_file)})
    assert decision.effect == "allow"


def test_permission_checker_blocks_destructive_out_of_scope(tmp_path: Path) -> None:
    """Destructive tool on out-of-scope path is denied."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ext_file = Path(tmp_path.anchor) / "xhx_outside_sandbox" / "config.yaml"

    sandbox = PathSandbox(workspace)
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=sandbox,
        rule_engine=RuleEngine(),
        mode=PermissionMode.DEFAULT,
    )

    decision = checker.check(
        "apply_patch",
        {"patch": f"*** Begin Patch\n*** Add File: {ext_file}\n+hello\n*** End Patch\n"},
        tool_category="write",
    )
    assert decision.effect == "deny"


def _cmd_checker(tmp_path: Path, mode: PermissionMode) -> PermissionChecker:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(workspace),
        rule_engine=RuleEngine(),
        mode=mode,
    )


def test_dangerous_detector_self_kill_patterns() -> None:
    """按映像名/进程名杀 python = agent 自杀，必须被绝对禁令层捕获；按 PID 杀不误伤。"""
    det = DangerousCommandDetector()
    assert det.detect("taskkill /f /im python.exe")[0]
    assert det.detect("taskkill /IM PythonW.exe")[0]
    assert det.detect("killall python")[0]
    assert det.detect("pkill -9 python3")[0]
    # 按 PID 杀、杀别的进程，不该误伤（仍可用来停具体子进程，如 dev server）
    assert not det.detect("taskkill /pid 1234 /f")[0]
    assert not det.detect("taskkill /im node.exe")[0]
    assert not det.detect("kill 1234")[0]


def test_self_kill_denied_in_all_modes(tmp_path: Path) -> None:
    """回归：``taskkill /f /im python.exe`` 会把 agent 自身（python.exe）强杀，

    导致终端卡在鼠标上报模式喷乱码。历史 bug 是 bypass 短路在所有安全层之前；
    现在绝对禁令层在短路之前，任何模式（含 bypass / dontAsk）都拦。
    """
    for mode in (PermissionMode.BYPASS, PermissionMode.DONT_ASK, PermissionMode.DEFAULT):
        d = _cmd_checker(tmp_path, mode).check(
            "Bash", {"command": "taskkill /f /im python.exe 2>nul"}, tool_category="command"
        )
        assert d.effect == "deny", mode
    for cmd in ("killall python", "pkill -9 python3"):
        d = _cmd_checker(tmp_path, PermissionMode.BYPASS).check("Bash", {"command": cmd}, tool_category="command")
        assert d.effect == "deny", cmd


def test_bypass_allows_kill_by_pid(tmp_path: Path) -> None:
    """设计意图：bypass 是用户主动全放行，按 PID 停子进程放行；只有「按映像名杀 python」
    这类自杀绝对禁令在 bypass 下也拦。"""
    d = _cmd_checker(tmp_path, PermissionMode.BYPASS).check(
        "Bash", {"command": "taskkill /pid 12345 /f"}, tool_category="command"
    )
    assert d.effect == "allow"


def test_risk_gate_unifies_with_decide_terminal(tmp_path: Path) -> None:
    """Layer 1c：非 bypass 模式下 checker 与 decide_terminal 统一——risk.py 判 DENY 的命令
    （含按 PID 杀的 taskkill）在 checker 里也 deny，消除「一条闸门拦一条漏」。"""
    d = _cmd_checker(tmp_path, PermissionMode.DEFAULT).check(
        "Bash", {"command": "taskkill /pid 12345 /f"}, tool_category="command"
    )
    assert d.effect == "deny"
