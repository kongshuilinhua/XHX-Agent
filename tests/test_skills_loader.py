"""skills/loader.py 单测：触发器匹配 + 新旧两种 Skill 加载 API。"""

from __future__ import annotations

from pathlib import Path

from xhx_agent.skills.loader import SkillLoader


def _make_skill(workspace: Path, name: str, triggers: list[str], desc: str = "d") -> None:
    skill_dir = workspace / ".xhx" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    trig_yaml = "\n".join(f"  - {t}" for t in triggers)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\ntriggers:\n{trig_yaml}\n---\n# {name} SOP\n步骤一二三\n",
        encoding="utf-8",
    )


def test_matches_trigger_word_boundary() -> None:
    loader = SkillLoader(Path("."))
    assert loader.matches_trigger("deploy", "please deploy now") is True
    assert loader.matches_trigger("deploy", "deployment pipeline") is False  # 单词边界
    assert loader.matches_trigger("deploy", "no match here") is False
    # 正则型 trigger
    assert loader.matches_trigger(r"v\d+", "release v2 now") is True


def test_load_available_and_match(tmp_path: Path) -> None:
    _make_skill(tmp_path, "deployer", ["deploy", "release"])
    loader = SkillLoader(tmp_path)
    available = loader.load_available_skills()
    assert any(s.name == "deployer" for s in available)

    matched = loader.match_skills("帮我 deploy 到生产")
    assert matched and matched[0].name == "deployer"
    assert matched[0].content and "SOP" in matched[0].content

    # 不含触发词 → 无匹配
    assert loader.match_skills("写个函数") == []


def test_load_available_empty(tmp_path: Path) -> None:
    # 无 skills 目录 → 空
    assert SkillLoader(tmp_path).load_available_skills() == []


def test_load_all_new_api_and_get(tmp_path: Path) -> None:
    _make_skill(tmp_path, "deployer", ["deploy"])
    loader = SkillLoader(tmp_path)
    skills = loader.load_all()
    assert "deployer" in skills
    got = loader.get("deployer")
    assert got is not None and got.name == "deployer"
    catalog = dict(loader.get_catalog())
    assert "deployer" in catalog
    # 未知 → None
    assert loader.get("nonexistent-skill") is None
