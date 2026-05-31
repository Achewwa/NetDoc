"""NetDoc agent controller."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm import LLMClient
from .planner import LLMPlanner, SkillCall
from .registry import SkillRegistry
from .skill import JsonDict


@dataclass(frozen=True)
class AgentResult:
    """Complete result from one user-agent interaction."""

    answer: str
    plan: SkillCall
    observation: JsonDict
    report_observation: JsonDict | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for CLI debug output."""
        result: dict[str, Any] = {
            "answer": self.answer,
            "plan": {
                "skill": self.plan.skill_name,
                "arguments": self.plan.arguments,
                "reason": self.plan.reason,
            },
            "observation": self.observation,
        }
        if self.report_observation is not None:
            result["report_observation"] = self.report_observation
        return result


@dataclass(frozen=True)
class NetDocAgent:
    """Plan a skill call, execute it, then synthesize a user-facing answer."""

    registry: SkillRegistry
    llm: LLMClient

    def answer(self, question: str) -> AgentResult:
        """Handle one natural-language network diagnosis question."""
        planner = LLMPlanner(self.llm)
        plan = planner.plan(question, self.registry)
        observation = self.registry.get(plan.skill_name).run(plan.arguments)
        answer = self._synthesize(question, plan, observation)
        report_observation = self._generate_report(question, plan, observation, answer)
        return AgentResult(
            answer=answer,
            plan=plan,
            observation=observation,
            report_observation=report_observation,
        )

    def _synthesize(self, question: str, plan: SkillCall, observation: JsonDict) -> str:
        response = self.llm.complete(
            system=(
                "You are NetDoc's diagnosis explainer. Use only the JSON observation "
                "as evidence. Answer in concise Chinese. State the conclusion first, "
                "then cite the key checks and safe next steps. Do not invent repair actions."
            ),
            user=_synthesis_prompt(question, plan, observation),
            max_tokens=800,
            temperature=0.0,
        )
        return response.strip()

    def _generate_report(
        self,
        question: str,
        plan: SkillCall,
        observation: JsonDict,
        answer: str,
    ) -> JsonDict | None:
        if plan.skill_name == "report_generator" or not self.registry.has("report_generator"):
            return None
        return self.registry.get("report_generator").run(
            {
                "user_question": question,
                "skill_calls": [_skill_call_json(plan)],
                "observations": [observation],
                "diagnosis_conclusion": answer,
                "repair_actions": [],
                "retest_results": [],
                "unresolved_issues": [],
            }
        )


def _synthesis_prompt(question: str, plan: SkillCall, observation: JsonDict) -> str:
    return (
        "User question:\n"
        f"{question}\n\n"
        "Executed skill call:\n"
        f"{json.dumps(_skill_call_json(plan), ensure_ascii=False)}\n\n"
        "Observation JSON:\n"
        f"{json.dumps(observation, ensure_ascii=False, indent=2)}\n\n"
        "Write a short diagnosis report in Chinese. Include: conclusion, important evidence "
        "from successful or failed checks, and low-risk next steps when the observation "
        "supports them. If all checks succeeded, say the checked path is currently normal. "
        "If some checks failed, state the most specific conclusion, such as DNS resolution "
        "failure, DNS works but direct public-IP access fails, HTTPS reachable but SSH "
        "unavailable, or only configuration visibility is incomplete."
    )


def _skill_call_json(plan: SkillCall) -> dict[str, JsonDict | str]:
    return {"skill": plan.skill_name, "arguments": plan.arguments}
