from xhx_agent.tui.format import human_tokens, context_meter

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
