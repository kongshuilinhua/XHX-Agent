from __future__ import annotations

import json
import re
from pathlib import Path

from xhx_agent.skills.metadata import Skill


class SkillLoader:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.skills_dir = workspace / ".xhx" / "skills"

    def matches_trigger(self, trigger: str, task: str) -> bool:
        # Enforce word boundary regex matching to avoid substring collision bugs
        pattern_str = r"\b" + re.escape(trigger) + r"\b" if re.match(r"^[\w\s-]+$", trigger) else trigger
        try:
            return bool(re.search(pattern_str, task, re.IGNORECASE))
        except re.error:
            try:
                return bool(re.search(r"\b" + re.escape(trigger) + r"\b", task, re.IGNORECASE))
            except re.error:
                return False

    def load_available_skills(self) -> list[Skill]:
        """Scan .xhx/skills/ directory and load metadata for all skills (lazy loading)."""
        skills: list[Skill] = []
        if not self.skills_dir.exists() or not self.skills_dir.is_dir():
            return skills

        def _parse_yaml_frontmatter(content: str) -> dict:
            match = re.match(r"^---\s*(?:yaml)?\r?\n(.*?)\r?\n---\r?\n", content, re.DOTALL | re.IGNORECASE)
            if not match:
                return {}
            yaml_text = match.group(1)
            import yaml
            try:
                data = yaml.safe_load(yaml_text)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
            return {}

        for path in self.skills_dir.iterdir():
            if path.is_dir():
                skill_loaded = False
                md_path = path / "SKILL.md"
                if md_path.exists() and md_path.is_file():
                    try:
                        content = md_path.read_text(encoding="utf-8")
                        data = _parse_yaml_frontmatter(content)
                        if data and "name" in data and data.get("triggers"):
                            skill = Skill(
                                name=data.get("name", path.name),
                                description=data.get("description", ""),
                                triggers=data.get("triggers", []),
                                permissions=data.get("permissions", {}),
                                folder_name=path.name,
                                content=None
                            )
                            skills.append(skill)
                            skill_loaded = True
                    except OSError:
                        pass

                if not skill_loaded:
                    json_path = path / "SKILL.json"
                    if json_path.exists() and json_path.is_file():
                        try:
                            with open(json_path, encoding="utf-8") as f:
                                data = json.load(f)
                            skill = Skill(
                                name=data.get("name", path.name),
                                description=data.get("description", ""),
                                triggers=data.get("triggers", []),
                                permissions=data.get("permissions", {}),
                                folder_name=path.name,
                                content=None
                            )
                            skills.append(skill)
                        except (json.JSONDecodeError, OSError):
                            continue
        return skills

    def match_skills(self, task: str) -> list[Skill]:
        """Find skills that trigger for the given task, and load their SKILL.md contents."""
        available_skills = self.load_available_skills()
        matched_skills: list[Skill] = []

        for skill in available_skills:
            matched = False
            for trigger in skill.triggers:
                if self.matches_trigger(trigger, task):
                    matched = True
                    break

            if matched:
                # O(1) path resolution using the cached folder_name, preventing N^2 scanning
                md_path = self.skills_dir / skill.folder_name / "SKILL.md"

                if md_path.exists() and md_path.is_file():
                    try:
                        with open(md_path, encoding="utf-8") as f:
                            skill.content = f.read()
                    except OSError:
                        skill.content = f"Error reading SKILL.md for {skill.name}"
                else:
                    skill.content = f"No SKILL.md found for {skill.name}"
                matched_skills.append(skill)

        return matched_skills
