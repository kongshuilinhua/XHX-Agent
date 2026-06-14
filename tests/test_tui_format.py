from xhx_agent.tui.format import context_meter, human_tokens, line_style


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
