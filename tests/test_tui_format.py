from xhx_agent.tui.format import context_meter, human_tokens, line_style, strip_system_reminder


def test_human_tokens() -> None:
    assert human_tokens(999) == "999"
    assert human_tokens(1000) == "1k"
    assert human_tokens(8800) == "8.8k"
    assert human_tokens(17835) == "17.8k"
    assert human_tokens(128000) == "128k"
    assert human_tokens(0) == "0"
    assert human_tokens(-5) == "-5"


def test_context_meter_ok() -> None:
    label, pct, level = context_meter(8800, 128000)
    assert "8.8k/128k" in label
    assert "6.9%" in label
    assert pct is not None
    assert round(pct, 3) == 6.875
    assert level == "ok"


def test_context_meter_warn() -> None:
    label, pct, level = context_meter(100000, 128000)
    assert pct is not None
    assert level == "warn"


def test_context_meter_crit() -> None:
    label, pct, level = context_meter(120000, 128000)
    assert pct is not None
    assert level == "crit"


def test_context_meter_none() -> None:
    label, pct, level = context_meter(0, 0)
    assert label == "Context —"
    assert pct is None
    assert level == "none"

    label2, pct2, level2 = context_meter(100, -1)
    assert label2 == "Context —"
    assert pct2 is None
    assert level2 == "none"


def test_line_style() -> None:
    # Test prefix-based matching
    assert line_style("user> hello") == "cyan"
    assert line_style("assistant> world") == "bright_white"
    assert line_style("system> init") == "yellow"
    assert line_style("plan> do something") == "blue"
    assert line_style("summary> done") == "dim"
    assert line_style("model (streaming...) 123") == "dim italic"

    # Test left-stripped first character/symbol matching
    assert line_style("⟶ tool_call") == "blue"
    assert line_style("  ⟶ tool_call_indented") == "blue"
    assert line_style("  · deepseek · 2.4s ...") == "dim"
    assert line_style("── turn 2 · deepseek · ...") == "dim"
    assert line_style("✓ success") == "green"
    assert line_style("  ✓ success_indented") == "green"
    assert line_style("✗ failure") == "red"
    assert line_style("  ✗ failure_indented") == "red"
    assert line_style("⚙ verify") == "blue"
    assert line_style("  ⚙ verify_indented") == "blue"
    assert line_style("▸ agent subagent") == "magenta"
    assert line_style("  ▸ agent subagent_indented") == "magenta"
    assert line_style("💭 thinking") == "dim italic"
    assert line_style("  💭 thinking_indented") == "dim italic"

    # Test default case
    assert line_style("ordinary text") == ""
    assert line_style("") == ""


def test_strip_system_reminder() -> None:
    # 成对标签整段剥掉，保留前后正常文本
    assert strip_system_reminder("前面<system-reminder>Plan mode is active...</system-reminder>后面") == "前面后面"
    # 多个块都剥掉
    assert strip_system_reminder("<system-reminder>a</system-reminder>x<system-reminder>b</system-reminder>") == "x"
    # 跨行（模型复述的多行提示）
    multiline = "好的\n<system-reminder>\nPlan mode is active.\n## Plan File Info\n</system-reminder>\n开始"
    assert "system-reminder" not in strip_system_reminder(multiline)
    assert "Plan mode" not in strip_system_reminder(multiline)
    # 未闭合的开标签（流式途中）→ 从开标签截断到结尾
    assert strip_system_reminder("正常内容<system-reminder>Plan mode is acti") == "正常内容"
    # 无标签时原样返回
    assert strip_system_reminder("普通回复") == "普通回复"
