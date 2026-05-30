"""Skill abstractions for NetDoc."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class Skill:
    """A JSON-described callable capability exposed to the agent."""

    name: str
    skill_schema: JsonDict
    implement_function: Callable[..., JsonDict]

    def run(self, arguments: JsonDict | None = None) -> JsonDict:
        """Execute the skill with JSON-compatible arguments."""
        return self.implement_function(**(arguments or {}))
