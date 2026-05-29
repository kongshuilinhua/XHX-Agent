from __future__ import annotations

from pydantic import BaseModel, Field


class SkillMetadata(BaseModel):
    name: str
    description: str
    triggers: list[str] = Field(default_factory=list)
    permissions: dict[str, str] = Field(default_factory=dict)
    folder_name: str = ""

class Skill(SkillMetadata):
    content: str | None = None
