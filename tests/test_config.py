from xhx_agent.runtime.config import default_config


def test_config_load_defaults() -> None:
    cfg = default_config()
    assert cfg.version == 1
    assert cfg.max_loop_turns == 20
    assert cfg.default_permission_mode == "default"
