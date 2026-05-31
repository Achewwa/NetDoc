"""Course-friendly diagnosis report generation skill."""

from __future__ import annotations

from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation


SKILL_NAME = "report_generator"


REPORT_GENERATOR_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Generate a course-presentation-friendly diagnosis report from user question, "
        "skill calls, observations, evidence, conclusion, repair actions, retest "
        "results, and unresolved issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_question": {
                "type": "string",
                "minLength": 1,
                "description": "Original user network problem.",
            },
            "skill_calls": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Skill call records, each usually containing skill, arguments and reason.",
            },
            "observations": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Observation JSON objects produced by diagnosis skills.",
            },
            "diagnosis_conclusion": {
                "type": "string",
                "description": "Optional final diagnosis conclusion. If omitted, infer from observations.",
            },
            "repair_actions": {
                "type": "array",
                "items": {},
                "description": "Repair actions taken or recommended.",
            },
            "retest_results": {
                "type": "array",
                "items": {},
                "description": "Verification or retest results after repair.",
            },
            "unresolved_issues": {
                "type": "array",
                "items": {},
                "description": "Known remaining issues or uncertainties.",
            },
            "title": {
                "type": "string",
                "minLength": 1,
                "description": "Report title.",
            },
        },
        "required": ["user_question", "observations"],
        "additionalProperties": False,
    },
}


def generate_report(
    user_question: str,
    observations: list[dict[str, Any]],
    skill_calls: list[dict[str, Any]] | None = None,
    diagnosis_conclusion: str = "",
    repair_actions: list[Any] | None = None,
    retest_results: list[Any] | None = None,
    unresolved_issues: list[Any] | None = None,
    title: str = "NetDoc 网络诊断报告",
) -> Observation:
    """Convert structured observations into a presentation-ready Markdown report."""
    normalized_skill_calls = skill_calls or _skill_calls_from_observations(observations)
    normalized_repairs = repair_actions or []
    normalized_retests = retest_results or []
    normalized_unresolved = unresolved_issues or []

    sections = {
        "用户问题": user_question,
        "调用了哪些 skill": _format_skill_calls(normalized_skill_calls),
        "每一步证据": _format_observation_evidence(observations),
        "诊断结论": diagnosis_conclusion.strip() or _infer_conclusion(observations),
        "修复动作": _format_freeform_items(normalized_repairs, empty_text="尚未执行修复动作。"),
        "复测结果": _format_freeform_items(normalized_retests, empty_text="尚未提供复测结果。"),
        "仍未解决的问题": _format_freeform_items(
            normalized_unresolved,
            empty_text="当前 observation 未显示仍未解决的问题。",
        ),
    }
    markdown = _render_markdown_report(title, sections)
    checks = _report_checks(observations, sections)
    status = "abnormal" if normalized_unresolved or _has_abnormal_observation(observations) else "normal"

    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary="已生成包含问题、Skill、证据、结论、修复、复测和遗留问题的展示报告。",
        risk_level="none",
        metadata={
            "title": title,
            "sections": sections,
            "report_markdown": markdown,
            "observation_count": len(observations),
            "called_skills": _called_skill_names(normalized_skill_calls, observations),
        },
    )


def _skill_calls_from_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for observation in observations:
        skill_name = observation.get("skill")
        if isinstance(skill_name, str) and skill_name:
            calls.append({"skill": skill_name, "arguments": {}, "reason": "来自 observation 记录"})
    return calls


def _format_skill_calls(skill_calls: list[dict[str, Any]]) -> list[str]:
    if not skill_calls:
        return ["未记录 Skill 调用。"]

    lines: list[str] = []
    for index, call in enumerate(skill_calls, start=1):
        skill_name = str(call.get("skill") or call.get("skill_name") or "unknown")
        arguments = call.get("arguments", {})
        reason = str(call.get("reason", "")).strip()
        line = f"{index}. {skill_name}"
        if isinstance(arguments, dict) and arguments:
            line += f" 参数: {_compact_mapping(arguments)}"
        if reason:
            line += f"；原因: {reason}"
        lines.append(line)
    return lines


def _format_observation_evidence(observations: list[dict[str, Any]]) -> list[str]:
    if not observations:
        return ["未提供 observation。"]

    evidence_lines: list[str] = []
    for observation in observations:
        skill_name = str(observation.get("skill", "unknown"))
        status = str(observation.get("status", "unknown"))
        summary = str(observation.get("summary", "")).strip()
        prefix = f"{skill_name} [{status}]"
        if summary:
            evidence_lines.append(f"{prefix}: {summary}")
        else:
            evidence_lines.append(prefix)

        checks = observation.get("checks", [])
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                name = str(check.get("name", "unknown_check"))
                success = "通过" if check.get("success") else "失败"
                evidence = str(check.get("evidence", "")).strip()
                evidence_lines.append(f"  - {name} [{success}]: {evidence}")
    return evidence_lines


def _infer_conclusion(observations: list[dict[str, Any]]) -> str:
    abnormal_summaries = [
        str(observation.get("summary", "")).strip()
        for observation in observations
        if observation.get("status") in {"abnormal", "error"} and observation.get("summary")
    ]
    if abnormal_summaries:
        return "；".join(abnormal_summaries)

    normal_summaries = [
        str(observation.get("summary", "")).strip()
        for observation in observations
        if observation.get("summary")
    ]
    if normal_summaries:
        return "已执行的诊断项未发现异常：" + "；".join(normal_summaries)
    return "当前证据不足，无法形成明确诊断结论。"


def _format_freeform_items(items: list[Any], *, empty_text: str) -> list[str]:
    if not items:
        return [empty_text]

    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            title = str(item.get("action") or item.get("name") or item.get("title") or f"项目 {index}")
            result = str(item.get("result") or item.get("summary") or item.get("evidence") or "").strip()
            line = f"{index}. {title}"
            if result:
                line += f": {result}"
            lines.append(line)
        else:
            lines.append(f"{index}. {item}")
    return lines


def _render_markdown_report(title: str, sections: dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    for section_title, content in sections.items():
        lines.append(f"## {section_title}")
        if isinstance(content, list):
            for item in content:
                text = str(item)
                if text.startswith("  - "):
                    lines.append(text)
                elif _starts_with_numbered_prefix(text):
                    lines.append(text)
                else:
                    lines.append(f"- {text}")
        else:
            lines.append(str(content))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _report_checks(observations: list[dict[str, Any]], sections: dict[str, Any]) -> list[CheckResult]:
    return [
        make_check(
            "report_sections",
            True,
            "报告已覆盖: " + ", ".join(sections.keys()),
            details={"section_count": len(sections)},
        ),
        make_check(
            "observation_evidence",
            bool(observations),
            f"报告汇总了 {len(observations)} 条 observation。",
            details={"observation_count": len(observations)},
        ),
    ]


def _has_abnormal_observation(observations: list[dict[str, Any]]) -> bool:
    return any(observation.get("status") in {"abnormal", "error"} for observation in observations)


def _called_skill_names(
    skill_calls: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> list[str]:
    names: list[str] = []
    for call in skill_calls:
        name = call.get("skill") or call.get("skill_name")
        if isinstance(name, str) and name not in names:
            names.append(name)
    for observation in observations:
        name = observation.get("skill")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def _compact_mapping(mapping: dict[str, Any]) -> str:
    parts = [f"{key}={value!r}" for key, value in sorted(mapping.items())]
    return ", ".join(parts)


def _starts_with_numbered_prefix(text: str) -> bool:
    prefix, dot, rest = text.partition(".")
    return bool(dot and rest.startswith(" ") and prefix.isdigit())


skill = Skill(
    name=SKILL_NAME,
    skill_schema=REPORT_GENERATOR_SCHEMA,
    implement_function=generate_report,
)
