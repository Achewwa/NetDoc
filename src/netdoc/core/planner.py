"""LLM-backed planning for NetDoc skill calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm import LLMClient, LLMError
from .registry import SkillRegistry
from .skill import JsonDict, SchemaValidationError


@dataclass(frozen=True)
class SkillCall:
    """A planned skill invocation."""

    skill_name: str
    arguments: JsonDict
    reason: str = ""


class PlanningError(RuntimeError):
    """Raised when the LLM cannot produce a valid skill call."""


@dataclass(frozen=True)
class LLMPlanner:
    """Ask the LLM to choose exactly one registered skill call."""

    llm: LLMClient

    def plan(self, question: str, registry: SkillRegistry) -> SkillCall:
        """Return one validated skill call for the user's question."""
        response = self.llm.complete(
            system=(
                "You are NetDoc's planner. Choose one registered diagnostic skill. "
                "Return only a JSON object, without markdown or commentary."
            ),
            user=_planning_prompt(question, registry),
            max_tokens=700,
            temperature=0.0,
        )
        data = _extract_json(response)
        if data.get("skill") in (None, ""):
            reason = str(data.get("reason", "No suitable skill."))
            raise PlanningError(reason)

        skill_name = str(data["skill"])
        arguments = data.get("arguments", {})
        if not isinstance(arguments, dict):
            raise PlanningError("Planner returned non-object arguments.")
        if not registry.has(skill_name):
            raise PlanningError(f"Planner selected unknown skill: {skill_name}")

        skill = registry.get(skill_name)
        try:
            skill.validate_arguments(arguments)
        except SchemaValidationError as exc:
            raise PlanningError(f"Planner returned invalid arguments: {exc}") from exc

        return SkillCall(
            skill_name=skill_name,
            arguments=arguments,
            reason=str(data.get("reason", "")),
        )


def _planning_prompt(question: str, registry: SkillRegistry) -> str:
    schemas = json.dumps(registry.schemas(), ensure_ascii=False, indent=2)
    return (
        "User question:\n"
        f"{question}\n\n"
        "Available skill schemas:\n"
        f"{schemas}\n\n"
        "Return this JSON shape exactly:\n"
        '{"skill":"registered_skill_name","arguments":{...},"reason":"short reason"}\n\n'
        "For DNS, domain resolution, nameserver or IP resolution questions, prefer "
        'dns_diagnosis with a domain argument. '
        "For GitHub connectivity questions, prefer service_connectivity with host "
        '"github.com", ports [443, 22], and protocols ["tcp", "https"], unless the '
        "user specifically asks whether DNS resolution is working."
    )


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _strip_code_fence(stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMError("Planner response did not contain a JSON object.")

    try:
        data = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Planner response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("Planner response JSON must be an object.")
    return data


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
