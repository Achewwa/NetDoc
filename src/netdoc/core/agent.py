"""NetDoc agent controller."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from netdoc.utils.json_types import RiskLevel

from .llm import LLMClient
from .planner import LLMPlanner, SkillCall
from .registry import SkillRegistry
from .skill import JsonDict


DIAGNOSIS_STATUSES_REQUIRING_REPAIR = {"abnormal", "error"}


@dataclass(frozen=True)
class AgentResult:
    """Complete result from one user-agent interaction."""

    answer: str
    plan: SkillCall
    observation: JsonDict
    report_observation: JsonDict | None = None
    candidate_plan: list[SkillCall] = field(default_factory=list)
    observations: list[JsonDict] = field(default_factory=list)
    executed_steps: list[dict[str, Any]] = field(default_factory=list)
    repair_call: SkillCall | None = None
    repair_observation: JsonDict | None = None
    retest_call: SkillCall | None = None
    retest_observation: JsonDict | None = None
    unresolved_issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation for CLI debug output."""
        candidate_plan = self.candidate_plan or [self.plan]
        observations = self.observations or [self.observation]
        result: dict[str, Any] = {
            "answer": self.answer,
            "plan": {
                "mode": "multi_step",
                "candidate_skills": [_skill_call_json(call) for call in candidate_plan],
            },
            "executed_steps": self.executed_steps,
            "observations": observations,
            "observation": self.observation,
            "unresolved_issues": self.unresolved_issues,
        }
        if self.repair_call is not None:
            result["repair_plan"] = _skill_call_json(self.repair_call)
        if self.repair_observation is not None:
            result["repair_observation"] = self.repair_observation
        if self.retest_call is not None:
            result["retest_plan"] = _skill_call_json(self.retest_call)
        if self.retest_observation is not None:
            result["retest_observation"] = self.retest_observation
        if self.report_observation is not None:
            result["report_observation"] = self.report_observation
        return result


@dataclass(frozen=True)
class NetDocAgent:
    """Plan diagnosis calls, repair proven abnormalities, then synthesize an answer."""

    registry: SkillRegistry
    llm: LLMClient

    def answer(
        self,
        question: str,
        *,
        allowed_risk: RiskLevel = "none",
        execute_repair: bool = False,
        repair_confirmation: str = "",
    ) -> AgentResult:
        """Handle one natural-language network diagnosis question."""
        planner = LLMPlanner(self.llm)
        candidate_plan = planner.plan_candidates(question, self.registry)
        observations: list[JsonDict] = []
        executed_steps: list[dict[str, Any]] = []
        abnormal_results: list[tuple[SkillCall, JsonDict]] = []

        for call in candidate_plan:
            observation = self.registry.get(call.skill_name).run(call.arguments)
            observation_key = f"observations[{len(observations)}]"
            observations.append(observation)
            if _is_abnormal_observation(observation):
                abnormal_results.append((call, observation))
            executed_steps.append(
                _executed_step(
                    phase="diagnosis",
                    step=len(executed_steps) + 1,
                    call=call,
                    observation_key=observation_key,
                )
            )

        repair_call, repair_target = _repair_call_for_abnormal_results(abnormal_results)
        repair_observation: JsonDict | None = None
        retest_call: SkillCall | None = None
        retest_observation: JsonDict | None = None
        unresolved_issues = _unresolved_issues(abnormal_results, repair_target)

        if repair_call is not None and repair_target is not None:
            repair_call = _apply_repair_policy(
                repair_call,
                allowed_risk=allowed_risk,
                execute_repair=execute_repair,
                repair_confirmation=repair_confirmation,
            )
            repair_observation = self.registry.get(repair_call.skill_name).run(repair_call.arguments)
            executed_steps.append(
                _executed_step(
                    phase="repair",
                    step=len(executed_steps) + 1,
                    call=repair_call,
                    observation_key="repair_observation",
                )
            )

            if _repair_was_executed(repair_observation):
                retest_call = repair_target[0]
                retest_observation = self.registry.get(retest_call.skill_name).run(
                    retest_call.arguments
                )
                executed_steps.append(
                    _executed_step(
                        phase="retest",
                        step=len(executed_steps) + 1,
                        call=retest_call,
                        observation_key="retest_observation",
                    )
                )
                if _is_abnormal_observation(retest_observation):
                    unresolved_issues.append(
                        _unresolved_issue_for_repair_target(
                            repair_target,
                            repair_call,
                            repair_observation,
                            reason="修复已执行，但复测仍显示异常。",
                        )
                    )
            else:
                unresolved_issues.append(
                    _unresolved_issue_for_repair_target(
                        repair_target,
                        repair_call,
                        repair_observation,
                        reason="修复尚未真实执行，可在提高风险授权或开启执行后继续处理。",
                    )
                )
        elif abnormal_results:
            unresolved_issues.append(
                {
                    "reason": "当前异常没有可安全自动映射的修复动作。",
                    "skills": [call.skill_name for call, _ in abnormal_results],
                }
            )

        answer = self._synthesize(
            question,
            candidate_plan,
            observations,
            repair_call,
            repair_observation,
            retest_call,
            retest_observation,
            unresolved_issues,
        )
        report_observation = self._generate_report(
            question,
            candidate_plan,
            observations,
            answer,
            repair_call,
            repair_observation,
            retest_call,
            retest_observation,
            unresolved_issues,
        )
        if report_observation is not None:
            executed_steps.append(
                {
                    "phase": "report",
                    "step": len(executed_steps) + 1,
                    "skill": "report_generator",
                    "arguments": {"observations": "observations"},
                    "observation_key": "report_observation",
                }
            )

        return AgentResult(
            answer=answer,
            plan=candidate_plan[0],
            observation=observations[0],
            report_observation=report_observation,
            candidate_plan=candidate_plan,
            observations=observations,
            executed_steps=executed_steps,
            repair_call=repair_call,
            repair_observation=repair_observation,
            retest_call=retest_call,
            retest_observation=retest_observation,
            unresolved_issues=unresolved_issues,
        )

    def _synthesize(
        self,
        question: str,
        candidate_plan: list[SkillCall],
        observations: list[JsonDict],
        repair_call: SkillCall | None,
        repair_observation: JsonDict | None,
        retest_call: SkillCall | None,
        retest_observation: JsonDict | None,
        unresolved_issues: list[dict[str, Any]],
    ) -> str:
        response = self.llm.complete(
            system=(
                "You are NetDoc's diagnosis explainer. Use only the JSON observations "
                "as evidence. Answer in concise Chinese. Return plain text only, with no "
                "Markdown headings, tables or bullet lists. Use at most 4 short sentences: "
                "conclusion first, then diagnosis evidence, repair status, and retest or "
                "remaining issue if present. Do not invent repair actions."
            ),
            user=_synthesis_prompt(
                question,
                candidate_plan,
                observations,
                repair_call,
                repair_observation,
                retest_call,
                retest_observation,
                unresolved_issues,
            ),
            max_tokens=900,
            temperature=0.0,
        )
        return response.strip()

    def _generate_report(
        self,
        question: str,
        candidate_plan: list[SkillCall],
        observations: list[JsonDict],
        answer: str,
        repair_call: SkillCall | None,
        repair_observation: JsonDict | None,
        retest_call: SkillCall | None,
        retest_observation: JsonDict | None,
        unresolved_issues: list[dict[str, Any]],
    ) -> JsonDict | None:
        if not self.registry.has("report_generator"):
            return None

        report_observations = list(observations)
        if repair_observation is not None:
            report_observations.append(repair_observation)
        if retest_observation is not None:
            report_observations.append(retest_observation)

        retest_results: list[Any] = []
        if retest_call is not None and retest_observation is not None:
            retest_results.append(
                {
                    "skill": retest_call.skill_name,
                    "summary": retest_observation.get("summary", ""),
                    "status": retest_observation.get("status", ""),
                }
            )

        return self.registry.get("report_generator").run(
            {
                "user_question": question,
                "skill_calls": [_skill_call_json(call) for call in candidate_plan],
                "observations": report_observations,
                "diagnosis_conclusion": answer,
                "repair_actions": [_skill_call_json(repair_call)] if repair_call is not None else [],
                "retest_results": retest_results,
                "unresolved_issues": unresolved_issues,
            }
        )


def _synthesis_prompt(
    question: str,
    candidate_plan: list[SkillCall],
    observations: list[JsonDict],
    repair_call: SkillCall | None,
    repair_observation: JsonDict | None,
    retest_call: SkillCall | None,
    retest_observation: JsonDict | None,
    unresolved_issues: list[dict[str, Any]],
) -> str:
    return (
        "User question:\n"
        f"{question}\n\n"
        "Candidate diagnostic skill calls:\n"
        f"{json.dumps([_skill_call_json(call) for call in candidate_plan], ensure_ascii=False)}\n\n"
        "Diagnosis observations:\n"
        f"{json.dumps(observations, ensure_ascii=False, indent=2)}\n\n"
        "Repair skill call:\n"
        f"{json.dumps(_skill_call_json(repair_call) if repair_call else None, ensure_ascii=False)}\n\n"
        "Repair observation:\n"
        f"{json.dumps(repair_observation, ensure_ascii=False, indent=2)}\n\n"
        "Retest skill call:\n"
        f"{json.dumps(_skill_call_json(retest_call) if retest_call else None, ensure_ascii=False)}\n\n"
        "Retest observation:\n"
        f"{json.dumps(retest_observation, ensure_ascii=False, indent=2)}\n\n"
        "Unresolved issues:\n"
        f"{json.dumps(unresolved_issues, ensure_ascii=False, indent=2)}\n\n"
        "Write a concise Chinese diagnosis answer, not a report. Use plain text only, "
        "no Markdown. State whether abnormalities were found. If repair was only planned "
        "or blocked by risk/confirmation, say it was not executed. If repair was executed "
        "and retested, state the retest result. If multiple abnormalities remain, mention "
        "that more than one layer may need follow-up."
    )


def _skill_call_json(plan: SkillCall | None) -> dict[str, JsonDict | str] | None:
    if plan is None:
        return None
    return {"skill": plan.skill_name, "arguments": plan.arguments, "reason": plan.reason}


def _executed_step(
    *,
    phase: str,
    step: int,
    call: SkillCall,
    observation_key: str,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "step": step,
        "skill": call.skill_name,
        "arguments": call.arguments,
        "reason": call.reason,
        "observation_key": observation_key,
    }


def _is_abnormal_observation(observation: JsonDict) -> bool:
    return observation.get("status") in DIAGNOSIS_STATUSES_REQUIRING_REPAIR


def _repair_call_for_abnormal_results(
    abnormal_results: list[tuple[SkillCall, JsonDict]],
) -> tuple[SkillCall | None, tuple[SkillCall, JsonDict] | None]:
    for result in abnormal_results:
        repair_call = _repair_call_for_observation(result[0], result[1])
        if repair_call is not None:
            return repair_call, result
    return None, None


def _repair_call_for_observation(call: SkillCall, observation: JsonDict) -> SkillCall | None:
    skill_name = str(observation.get("skill") or call.skill_name)
    summary = str(observation.get("summary", ""))
    evidence_text = _observation_text(observation)

    if skill_name == "dns_diagnosis":
        return SkillCall(
            skill_name="repair_actions",
            arguments={"action": "flush_dns_cache"},
            reason="DNS 诊断异常时先执行低风险 DNS 缓存刷新计划。",
        )
    if skill_name == "proxy_vpn_diagnosis":
        if "git proxy" in evidence_text:
            return SkillCall(
                skill_name="repair_actions",
                arguments={
                    "action": "clear_git_proxy_config",
                    "options": {"scope": _git_proxy_scope(observation)},
                },
                reason="代理/VPN 诊断发现残留 Git proxy 配置，优先清理 Git proxy。",
            )
        action = "disable_system_proxy" if "系统代理" in summary else "restart_proxy_process"
        return SkillCall(
            skill_name="repair_actions",
            arguments={"action": action},
            reason="代理/VPN 诊断异常，选择对应的中风险代理修复计划。",
        )
    if skill_name == "link_status":
        interface = _first_interface_name(observation)
        options = {"interface": interface} if interface else {}
        return SkillCall(
            skill_name="repair_actions",
            arguments={"action": "restart_network_interface", "options": options},
            reason="链路诊断异常，网卡重启属于高风险修复，需要明确授权。",
        )
    return None


def _observation_text(observation: JsonDict) -> str:
    parts = [str(observation.get("summary", ""))]
    for check in observation.get("checks", []):
        if not isinstance(check, dict):
            continue
        parts.append(str(check.get("name", "")))
        parts.append(str(check.get("evidence", "")))
        details = check.get("details")
        if isinstance(details, dict):
            parts.append(json.dumps(details, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts).casefold()


def _git_proxy_scope(observation: JsonDict) -> str:
    text = _observation_text(observation)
    has_local = "local." in text
    has_global = "global." in text
    if has_local and has_global:
        return "all"
    if has_global:
        return "global"
    return "local"


def _first_interface_name(observation: JsonDict) -> str:
    metadata = observation.get("metadata")
    if isinstance(metadata, dict):
        for key in ("interface", "adapter", "active_interface"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    for check in observation.get("checks", []):
        if not isinstance(check, dict):
            continue
        details = check.get("details")
        if not isinstance(details, dict):
            continue
        for key in ("interface", "adapter", "name"):
            value = details.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _apply_repair_policy(
    plan: SkillCall,
    *,
    allowed_risk: RiskLevel,
    execute_repair: bool,
    repair_confirmation: str,
) -> SkillCall:
    arguments = dict(plan.arguments)
    arguments["allowed_risk"] = allowed_risk
    arguments["execute"] = execute_repair
    if repair_confirmation:
        arguments["confirmation"] = repair_confirmation
    return SkillCall(skill_name=plan.skill_name, arguments=arguments, reason=plan.reason)


def _repair_was_executed(repair_observation: JsonDict) -> bool:
    metadata = repair_observation.get("metadata")
    return isinstance(metadata, dict) and metadata.get("executed") is True


def _unresolved_issue_for_repair_target(
    repair_target: tuple[SkillCall, JsonDict],
    repair_call: SkillCall,
    repair_observation: JsonDict,
    *,
    reason: str,
) -> dict[str, Any]:
    target_call, target_observation = repair_target
    return {
        "skill": target_call.skill_name,
        "status": target_observation.get("status", ""),
        "summary": target_observation.get("summary", ""),
        "reason": reason,
        "repair_plan": _skill_call_json(repair_call),
        "repair_summary": repair_observation.get("summary", ""),
    }


def _unresolved_issues(
    abnormal_results: list[tuple[SkillCall, JsonDict]],
    repair_target: tuple[SkillCall, JsonDict] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for call, observation in abnormal_results:
        if repair_target is not None and call == repair_target[0]:
            continue
        issues.append(
            {
                "skill": call.skill_name,
                "status": observation.get("status", ""),
                "summary": observation.get("summary", ""),
                "reason": "该异常已记录，但本轮只自动尝试一个可映射修复动作。",
            }
        )
    return issues
