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
