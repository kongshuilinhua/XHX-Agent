"""Skill 加载器：三层加载（内置 < 用户 < 项目）+ 触发器匹配 + 热重载。

支持两种 Skill 格式：
    - 旧格式：目录内有 SKILL.md（含 triggers frontmatter），无 name 也适用
    - 新格式：SKILL.md（含 name/description/mode/context frontmatter）
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import re
import time
from pathlib import Path

from xhx_agent.skills.metadata import Skill
from xhx_agent.skills.parser import SkillDef, SkillParseError, parse_skill_file

logger = logging.getLogger(__name__)

# 路径约定
PROJECT_SKILLS_DIR = ".xhx/skills"
USER_SKILLS_DIR = "~/.xhx/skills"
BUILTINS_PACKAGE = "xhx_agent.skills.builtins"


class SkillLoader:
    """三层 Skill 加载器 + 触发器匹配。

    加载优先级（低→高）：
        1. 内置（builtins/ 包内 SKILL.md 文件）
        2. 用户级（~/.xhx/skills/）
        3. 项目级（.xhx/skills/）  ← 最高优先级
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.skills_dir = workspace / PROJECT_SKILLS_DIR
        self.user_skills_dir = Path(USER_SKILLS_DIR).expanduser()
        # 缓存：name → SkillDef + mtime
        self._cache: dict[str, tuple[SkillDef, float]] = {}

    # ------------------------------------------------------------------
    # trigger matching
    # ------------------------------------------------------------------

    def matches_trigger(self, trigger: str, task: str) -> bool:
        """单词边界正则匹配，防止子串碰撞。"""
        pattern_str = r"\b" + re.escape(trigger) + r"\b" if re.match(r"^[\w\s-]+$", trigger) else trigger
        try:
            return bool(re.search(pattern_str, task, re.IGNORECASE))
        except re.error:
            try:
                return bool(re.search(r"\b" + re.escape(trigger) + r"\b", task, re.IGNORECASE))
            except re.error:
                return False

    # ------------------------------------------------------------------
    # loading — 旧 Skill API（向后兼容）
    # ------------------------------------------------------------------

    def load_available_skills(self) -> list[Skill]:
        """扫描 .xhx/skills/ 目录，加载 Skill 元数据（旧 API）。"""
        skills: list[Skill] = []
        if not self.skills_dir.exists() or not self.skills_dir.is_dir():
            return skills

        for path in self.skills_dir.iterdir():
            if path.is_dir():
                skill = self._load_skill_from_dir(path)
                if skill:
                    skills.append(skill)
        return skills

    def match_skills(self, task: str) -> list[Skill]:
        """匹配触发词，加载 SKILL.md 内容（旧 API）。"""
        available_skills = self.load_available_skills()
        matched: list[Skill] = []

        for skill in available_skills:
            for trigger in skill.triggers:
                if self.matches_trigger(trigger, task):
                    md_path = self.skills_dir / skill.folder_name / "SKILL.md"
                    if md_path.exists() and md_path.is_file():
                        try:
                            skill.content = md_path.read_text(encoding="utf-8")
                        except OSError as e:
                            logger.warning("Failed to read SKILL.md %s: %s", md_path, e)
                            skill.content = f"Error reading SKILL.md for {skill.name}"
                    else:
                        skill.content = f"No SKILL.md found for {skill.name}"
                    matched.append(skill)
                    break  # 一个 skill 只触发一次

        return matched

    # ------------------------------------------------------------------
    # loading — 新 SkillDef API（三层 + 热重载）
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, SkillDef]:
        """加载全部 Skill 定义（三层，last-wins：项目 > 用户 > 内置）。"""
        seen: dict[str, SkillDef] = {}

        # 加载顺序：低优先级先，高优先级后——无条件覆盖实现 last-wins
        # 1. 内置（最低优先级）
        for skill_def in self._load_builtins():
            seen[skill_def.name] = skill_def

        # 2. 用户级（覆盖内置）
        for skill_def in self._scan_directory(self.user_skills_dir, "user"):
            seen[skill_def.name] = skill_def

        # 3. 项目级（最高优先级，覆盖用户和内置）
        for skill_def in self._scan_directory(self.skills_dir, "project"):
            seen[skill_def.name] = skill_def

        # 更新缓存
        for name, sd in seen.items():
            mtime = sd.source_path.stat().st_mtime if sd.source_path else time.time()
            self._cache[name] = (sd, mtime)

        return seen

    def get(self, name: str) -> SkillDef | None:
        """获取指定 Skill 定义（支持热重载）。"""
        entry = self._cache.get(name)
        if entry is None:
            # 未缓存 → 全量加载
            self.load_all()
            entry = self._cache.get(name)
            if entry is None:
                return None

        sd, cached_mtime = entry
        # 热重载：文件修改后自动重新解析
        if sd.source_path is not None and sd.source_path.exists():
            current_mtime = sd.source_path.stat().st_mtime
            if current_mtime > cached_mtime:
                try:
                    reloaded = parse_skill_file(sd.source_path)
                    self._cache[name] = (reloaded, current_mtime)
                    return reloaded
                except SkillParseError as e:
                    logger.warning("Hot reload failed for %s: %s", name, e)

        return sd

    def get_catalog(self) -> list[tuple[str, str]]:
        """返回 (name, description) 的技能目录列表。"""
        all_skills = self.load_all()
        return [(name, sd.description or "") for name, sd in all_skills.items()]

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _load_skill_from_dir(self, path: Path) -> Skill | None:
        """从目录加载旧格式 Skill。"""
        md_path = path / "SKILL.md"
        if md_path.exists() and md_path.is_file():
            try:
                content = md_path.read_text(encoding="utf-8")
                data = self._parse_yaml_frontmatter(content)
                if data and "name" in data:
                    return Skill(
                        name=data.get("name", path.name),
                        description=data.get("description", ""),
                        triggers=data.get("triggers", []),
                        permissions=data.get("permissions", {}),
                        folder_name=path.name,
                        content=None,
                    )
            except OSError as e:
                logger.warning("Failed to read %s: %s", md_path, e)

        # 回退：SKILL.json
        json_path = path / "SKILL.json"
        if json_path.exists() and json_path.is_file():
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = json.load(f)
                return Skill(
                    name=data.get("name", path.name),
                    description=data.get("description", ""),
                    triggers=data.get("triggers", []),
                    permissions=data.get("permissions", {}),
                    folder_name=path.name,
                    content=None,
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load %s: %s", json_path, e)

        return None

    @staticmethod
    def _parse_yaml_frontmatter(content: str) -> dict:
        """解析 YAML frontmatter（回退兼容——失败返回 {} 而非抛异常）。"""
        try:
            from xhx_agent.utils.frontmatter import parse_frontmatter

            return parse_frontmatter(content)[0]
        except Exception as e:
            logger.warning("Failed to parse YAML frontmatter: %s", e)
            return {}

    def _scan_directory(self, path: Path, source: str) -> list[SkillDef]:
        """扫描目录下的 SKILL.md 文件（新格式）。"""
        results: list[SkillDef] = []
        if not path.is_dir():
            return results

        for entry in sorted(path.iterdir()):
            md_path: Path | None = None
            if entry.is_dir():
                md_path = entry / "SKILL.md"
            elif entry.is_file() and entry.suffix == ".md":
                md_path = entry

            if md_path is None or not md_path.is_file():
                continue

            try:
                sd = parse_skill_file(md_path)
                sd.is_directory = entry.is_dir()
                results.append(sd)
            except SkillParseError as e:
                logger.warning("Skipping skill file %s: %s", md_path, e)

        return results

    def _load_builtins(self) -> list[SkillDef]:
        """加载内置 Skill（从包内 builtins/ 目录）。"""
        results: list[SkillDef] = []
        try:
            builtins_pkg = importlib.resources.files(BUILTINS_PACKAGE)
        except (ModuleNotFoundError, TypeError):
            return results

        for item in builtins_pkg.iterdir():
            if not item.name.endswith(".md"):
                continue
            try:
                sd = parse_skill_file(Path(str(item)))
                results.append(sd)
            except SkillParseError as e:
                logger.warning("Skipping built-in skill %s: %s", item.name, e)

        return results
