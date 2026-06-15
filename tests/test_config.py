from pathlib import Path

from xhx_agent.runtime.config import default_config


def test_config_load_defaults() -> None:
    cfg = default_config()
    assert cfg.version == 1
    assert cfg.max_loop_turns == 20
    assert cfg.default_permission_mode == "default"


def test_config_web_search_defaults() -> None:
    cfg = default_config()
    assert cfg.web_search.provider == "tavily"
    assert cfg.web_search.tavily_api_key == ""
    assert cfg.web_search.tavily_api_key_env == "TAVILY_API_KEY"
    assert cfg.web_search.max_results == 5


def test_config_write_and_load_web_search(tmp_path: Path) -> None:
    from xhx_agent.runtime.config import load_config, write_default_config

    success = write_default_config(tmp_path)
    assert success is True

    cfg = load_config(tmp_path)
    assert cfg.web_search.provider == "tavily"
    assert cfg.web_search.tavily_api_key == ""
