from pathlib import Path
from xhx_agent.runtime.config import default_config, load_config, ProjectConfig

def test_auto_resume_defaults_true() -> None:
    assert default_config().auto_resume is True

def test_auto_resume_roundtrip(tmp_path) -> None:
    from xhx_agent.runtime.config import config_path
    import json

    # Write config with auto_resume = False
    cfg = ProjectConfig(auto_resume=False)
    p = config_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.model_dump_json(), encoding="utf-8")

    loaded = load_config(tmp_path)
    assert loaded.auto_resume is False

    # Missing file fallback
    assert load_config(Path("nonexistent")).auto_resume is True
