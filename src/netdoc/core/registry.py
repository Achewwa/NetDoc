"""Skill registry for lookup and schema export."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .skill import JsonDict, Skill


class SkillRegistry:
    """Stores skills by name and exposes schemas for planner prompts."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill by its public name."""
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def register_many(self, skills: Iterable[Skill]) -> None:
        """Register multiple skills in order."""
        for skill in skills:
            self.register(skill)

    def has(self, name: str) -> bool:
        """Return whether a skill name is registered."""
        return name in self._skills

    def get(self, name: str) -> Skill:
        """Return one skill by exact name."""
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def find(self, query: str) -> list[Skill]:
        """Find skills whose name or description contains the query."""
        normalized = query.casefold()
        matches: list[Skill] = []
        for skill in self._skills.values():
            description = str(skill.skill_schema.get("description", ""))
            haystack = f"{skill.name}\n{description}".casefold()
            if normalized in haystack:
                matches.append(skill)
        return matches

    def names(self) -> list[str]:
        """Return registered skill names in registration order."""
        return list(self._skills)

    def schemas(self) -> list[JsonDict]:
        """Return schemas in registration order."""
        return [skill.skill_schema for skill in self._skills.values()]

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)
