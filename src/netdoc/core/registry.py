"""Skill registry for lookup and schema export."""

from __future__ import annotations

from .skill import JsonDict, Skill


class SkillRegistry:
    """Stores skills by name and exposes schemas for planner prompts."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def schemas(self) -> list[JsonDict]:
        return [skill.skill_schema for skill in self._skills.values()]
