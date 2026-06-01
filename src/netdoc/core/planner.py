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
    """Ask the LLM to choose registered skill calls."""

    llm: LLMClient

    def plan(self, question: str, registry: SkillRegistry) -> SkillCall:
        """Return one validated skill call for the user's question."""
        return self.plan_candidates(question, registry, max_candidates=1)[0]

    def plan_candidates(
        self,
        question: str,
        registry: SkillRegistry,
        *,
        max_candidates: int = 4,
    ) -> list[SkillCall]:
        """Return a validated ordered list of diagnostic skill calls."""
        response = self.llm.complete(
            system=(
                "You are NetDoc's planner. Choose an ordered diagnostic skill list. "
                "Return only a JSON object, without markdown or commentary."
            ),
            user=_planning_prompt(question, registry, max_candidates=max_candidates),
            max_tokens=1200,
            temperature=0.0,
        )
        data = _extract_json(response)
        candidates = _candidate_items(data)
        if not candidates:
            raise PlanningError(str(data.get("reason", "No suitable diagnostic skill.")))

        calls: list[SkillCall] = []
        seen: set[str] = set()
        for item in candidates:
            skill_name = str(item.get("skill") or item.get("skill_name") or "")
            if skill_name in {"repair_actions", "report_generator"}:
                continue
            if not skill_name:
                continue
            arguments = item.get("arguments", {})
            if not isinstance(arguments, dict):
                raise PlanningError("Planner returned non-object arguments.")
            seen_key = json.dumps(
                {"skill": skill_name, "arguments": arguments},
                ensure_ascii=False,
                sort_keys=True,
            )
            if seen_key in seen:
                continue
            if not registry.has(skill_name):
                raise PlanningError(f"Planner selected unknown skill: {skill_name}")

            skill = registry.get(skill_name)
            try:
                skill.validate_arguments(arguments)
            except SchemaValidationError as exc:
                raise PlanningError(f"Planner returned invalid arguments: {exc}") from exc

            seen.add(seen_key)
            calls.append(
                SkillCall(
                    skill_name=skill_name,
                    arguments=arguments,
                    reason=str(item.get("reason") or data.get("reason") or ""),
                )
            )
            if len(calls) >= max_candidates:
                break

        if not calls:
            raise PlanningError("Planner did not return a diagnostic skill.")
        return calls


def _planning_prompt(question: str, registry: SkillRegistry, *, max_candidates: int) -> str:
    schemas = json.dumps(registry.schemas(), ensure_ascii=False, indent=2)
    return (
        "User question:\n"
        f"{question}\n\n"
        "Available skill schemas:\n"
        f"{schemas}\n\n"
        "Return this JSON shape exactly:\n"
        '{"candidate_skills":[{"skill":"registered_diagnostic_skill_name",'
        '"arguments":{...},"reason":"short reason"}],"reason":"short overall reason"}\n\n'
        f"Return 1 to {max_candidates} candidate skills in the order they should run. "
        "Use diagnostic skills only in candidate_skills. Do not include repair_actions "
        "or report_generator; the agent controller decides repair and report steps "
        "after observations prove whether an abnormal condition exists. "
        "For DNS, domain resolution, nameserver or IP resolution questions, prefer "
        'dns_diagnosis with a domain argument. '
        "For GitHub connectivity questions, include service_connectivity with host "
        '"github.com", ports [443, 22], and protocols ["tcp", "https"]. For proxy, VPN, '
        "Clash, system proxy, Git proxy, http_proxy or https_proxy questions, prefer "
        "proxy_vpn_diagnosis. For latency, packet loss, ping, network quality, slow "
        "network or multi-target comparison questions, prefer network_quality. "
        "For speedtest, download throughput or jitter questions, explain with "
        "network_quality only if ping latency and packet loss are still useful. "
        "For explicit repair, fix, refresh DNS cache, clear cache, disable proxy, "
        "restart proxy, change DNS, route, or network adapter repair requests, "
        "first choose the diagnostic skill that can verify the problem, then add any "
        "adjacent diagnostic skills needed to avoid fixing the wrong layer. "
        "Use report_generator only when the user asks to produce or summarize a "
        "diagnosis report from existing observations."
    )


def _candidate_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = data.get("candidate_skills")
    if isinstance(candidates, list):
        return [item for item in candidates if isinstance(item, dict)]
    if data.get("skill") not in (None, ""):
        return [data]
    return []


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _strip_code_fence(stripped)

    decoder = json.JSONDecoder()
    for start, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            data, _ = decoder.raw_decode(stripped[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise LLMError("Planner response did not contain a valid JSON object.")


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
