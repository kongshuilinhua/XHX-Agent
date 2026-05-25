from pathlib import Path

from xhx_agent.runtime.config import load_config, write_default_config
from xhx_agent.runtime.profiles import get_profile, write_default_profiles


def test_default_config_and_profile(tmp_path: Path) -> None:
    assert write_default_config(tmp_path)
    assert write_default_profiles(tmp_path)
    config = load_config(tmp_path)
    profile = get_profile(tmp_path, config.default_profile)
    assert config.write_policy == "apply_patch_only"
    assert config.default_profile == "mock"
    assert profile.provider == "mock"
    assert get_profile(tmp_path, "default").provider == "openai-compatible"
