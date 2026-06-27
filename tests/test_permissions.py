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
    现在绝对禁令层在短路之前，任何模式（含 bypass）都拦。
    """
    for mode in (PermissionMode.BYPASS, PermissionMode.ACCEPT_EDITS, PermissionMode.DEFAULT):
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


def test_default_mode_asks_not_denies_noncatastrophic(tmp_path: Path) -> None:
    """回归：交互（非 bypass）模式下，「非绝对禁令」的有风险命令必须走 ask（让用户/上层裁决），

    不能在 checker 层硬 deny。否则 `cd X && python app.py` 这种启动 dev server 的日常操作、
    `netstat | findstr`、`curl 本地自测` 全被一刀切拦死（曾因接入 classify_command 误伤）。
    绝对禁令（自杀 / 格盘…）仍由 Layer 1b 在所有模式拦——见 test_self_kill_denied_in_all_modes。
    """
    checker = _cmd_checker(tmp_path, PermissionMode.DEFAULT)
    for cmd in (
        "cd /tmp/proj && python app.py",  # 启动 dev server（含 &&）
        "netstat -ano | findstr :5000",  # 查端口（含管道）
        "taskkill /pid 12345 /f",  # 按 PID 停子进程
        "curl -s http://127.0.0.1:5000/",  # 本地自测
    ):
        d = checker.check("Bash", {"command": cmd}, tool_category="command")
        assert d.effect == "ask", cmd


# --- 向 Claude 靠拢：命令子命令逐段评估 + 敏感路径豁免 ---


def _ruled_checker(tmp_path: Path, mode: PermissionMode, rules_yaml: str) -> PermissionChecker:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    rf = tmp_path / "permissions.local.yaml"
    rf.write_text(rules_yaml, encoding="utf-8")
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(workspace),
        rule_engine=RuleEngine(local_rules_path=rf),
        mode=mode,
    )


def test_command_all_subcommands_allowed(tmp_path: Path) -> None:
    c = _ruled_checker(
        tmp_path,
        PermissionMode.DEFAULT,
        '- rule: "Bash(mkdir:*)"\n  effect: allow\n- rule: "Bash(echo:*)"\n  effect: allow\n',
    )
    d = c.check("Bash", {"command": "mkdir foo & echo done"}, tool_category="command")
    assert d.effect == "allow"


def test_command_uncovered_subcommand_falls_to_mode(tmp_path: Path) -> None:
    # mkdir 被 allow，但 rm 无规则 → 不是「全部子命令都 allow」→ 落到 DEFAULT 模式（command=ask），
    # 堵住 `mkdir x && rm y` 这类借组合命令夹带未授权指令。
    c = _ruled_checker(tmp_path, PermissionMode.DEFAULT, '- rule: "Bash(mkdir:*)"\n  effect: allow\n')
    d = c.check("Bash", {"command": "mkdir foo && rm bar"}, tool_category="command")
    assert d.effect == "ask"


def test_command_deny_rule_beats_mode(tmp_path: Path) -> None:
    c = _ruled_checker(tmp_path, PermissionMode.ACCEPT_EDITS, '- rule: "Bash(rm:*)"\n  effect: deny\n')
    d = c.check("Bash", {"command": "rm -rf somedir"}, tool_category="command")
    assert d.effect == "deny"


def test_command_ask_rule(tmp_path: Path) -> None:
    c = _ruled_checker(tmp_path, PermissionMode.DEFAULT, '- rule: "Bash(git push:*)"\n  effect: ask\n')
    d = c.check("Bash", {"command": "git push origin main"}, tool_category="command")
    assert d.effect == "ask"


def test_sensitive_file_asks_in_auto_modes(tmp_path: Path) -> None:
    for mode in (PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS):
        c = _ruled_checker(tmp_path, mode, "")
        assert c.check("WriteFile", {"file_path": ".env"}, tool_category="write").effect == "ask", mode
        assert c.check("WriteFile", {"file_path": ".git/config"}, tool_category="write").effect == "ask", mode


def test_sensitive_file_normal_write_allowed_in_accept_edits(tmp_path: Path) -> None:
    c = _ruled_checker(tmp_path, PermissionMode.ACCEPT_EDITS, "")
    fp = str(tmp_path / "ws" / "foo.py")
    assert c.check("WriteFile", {"file_path": fp}, tool_category="write").effect == "allow"


# --- auto 模式：命令风险分级 + 写放行/敏感路径确认 ---


def test_auto_mode_command_classification(tmp_path: Path) -> None:
    c = _cmd_checker(tmp_path, PermissionMode.AUTO)
    # 只读 SAFE → 直接放行
    assert c.check("Bash", {"command": "ls -la"}, tool_category="command").effect == "allow"
    # 破坏性 DENY → 转 ask（不是硬 deny），无需 LLM
    d_rm = c.check("Bash", {"command": "rm foo"}, tool_category="command")
    assert d_rm.effect == "ask" and not d_rm.needs_classification
    # 拿不准 CONFIRM → ask + 标记交 LLM 分类器
    d_mk = c.check("Bash", {"command": "mkdir foo"}, tool_category="command")
    assert d_mk.effect == "ask" and d_mk.needs_classification
    # 组合命令任一段破坏性 → ask
    assert c.check("Bash", {"command": "mkdir foo && rm bar"}, tool_category="command").effect == "ask"


def test_auto_mode_write_allows_but_sensitive_asks(tmp_path: Path) -> None:
    c = _cmd_checker(tmp_path, PermissionMode.AUTO)
    fp = str(tmp_path / "workspace" / "x.py")
    assert c.check("WriteFile", {"file_path": fp}, tool_category="write").effect == "allow"
    assert c.check("WriteFile", {"file_path": ".env"}, tool_category="write").effect == "ask"
