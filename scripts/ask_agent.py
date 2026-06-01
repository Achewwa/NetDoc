"""Command-line entry point for the LLM-backed NetDoc agent."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from netdoc.core import AnthropicMessagesClient, LLMError, NetDocAgent, PlanningError  # noqa: E402
from netdoc.skills import create_default_registry  # noqa: E402
from netdoc.utils.json_types import RiskLevel  # noqa: E402


RISK_LEVELS = ("none", "low", "medium", "high")


@dataclass
class ConversationState:
    """In-memory state for one interactive CLI session."""

    last_question: str = ""
    last_answer: str = ""
    unresolved_issues: list[dict[str, Any]] = field(default_factory=list)
    last_result: dict[str, Any] | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ask the NetDoc LLM agent a diagnosis question."
    )
    parser.add_argument("question", nargs="*", help="Question to ask. Omit for interactive mode.")
    parser.add_argument("--base-url", help="Anthropic-compatible base URL.")
    parser.add_argument(
        "--model",
        help="Model name. Defaults to ANTHROPIC_MODEL or built-in default.",
    )
    parser.add_argument("--timeout", type=float, help="LLM request timeout in seconds.")
    parser.add_argument(
        "--show-json",
        action="store_true",
        help="Print plan and observation JSON after the answer.",
    )
    parser.add_argument(
        "--show-report",
        action="store_true",
        help="Print the report_generator Markdown report after the concise answer.",
    )
    parser.add_argument(
        "--allowed-risk",
        choices=RISK_LEVELS,
        default="none",
        help="Maximum repair risk level allowed for repair_actions. Defaults to none.",
    )
    parser.add_argument(
        "--execute-repair",
        action="store_true",
        help="Allow repair_actions to execute when risk policy and confirmation permit it.",
    )
    args = parser.parse_args()

    try:
        llm = AnthropicMessagesClient.from_env(
            base_url=args.base_url,
            model=args.model,
            timeout_seconds=args.timeout,
        )
        agent = NetDocAgent(registry=create_default_registry(), llm=llm)
    except LLMError as exc:
        print(f"LLM 配置错误：{exc}", file=sys.stderr)
        return 2

    if args.question:
        return _ask_once(
            agent,
            " ".join(args.question),
            show_json=args.show_json,
            show_report=args.show_report,
            allowed_risk=args.allowed_risk,
            execute_repair=args.execute_repair,
        )
    return _interactive_loop(
        agent,
        show_json=args.show_json,
        show_report=args.show_report,
        allowed_risk=args.allowed_risk,
        execute_repair=args.execute_repair,
    )


def _ask_once(
    agent: NetDocAgent,
    question: str,
    *,
    show_json: bool,
    show_report: bool,
    allowed_risk: RiskLevel = "none",
    execute_repair: bool = False,
    repair_confirmation: str = "",
    state: ConversationState | None = None,
) -> int:
    try:
        result = agent.answer(
            question,
            allowed_risk=allowed_risk,
            execute_repair=execute_repair,
            repair_confirmation=repair_confirmation,
        )
    except (LLMError, PlanningError, KeyError, ValueError) as exc:
        print(f"诊断失败：{exc}", file=sys.stderr)
        return 1

    display_text = _display_text(result, show_report=show_report)
    if show_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    print(display_text)
    if state is not None:
        _update_conversation_state(state, question, result)
    return 0


def _interactive_loop(
    agent: NetDocAgent,
    *,
    show_json: bool,
    show_report: bool,
    allowed_risk: RiskLevel,
    execute_repair: bool,
) -> int:
    current_allowed_risk = allowed_risk
    current_execute_repair = execute_repair
    state = ConversationState()
    print(
        "NetDoc 交互模式，输入 exit 或 quit 退出；"
        "输入 /risk 查看风险等级，/risk none|low|medium|high 切换；"
        "输入 /repair on|off 切换是否执行修复；"
        "输入 /continue 继续处理未解决项。"
    )
    print(f"当前允许修复风险等级: {current_allowed_risk}")
    print(f"当前修复执行模式: {'execute' if current_execute_repair else 'dry-run'}")
    while True:
        try:
            question = input("> ").strip()
        except EOFError:
            print()
            return 0
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            return 0
        risk_command = _handle_risk_command(question, current_allowed_risk)
        if risk_command is not None:
            current_allowed_risk = risk_command
            continue
        repair_command = _handle_repair_command(question, current_execute_repair)
        if repair_command is not None:
            current_execute_repair = repair_command
            continue
        continue_question = _handle_continue_command(question, state)
        if continue_question is not None:
            if continue_question:
                exit_code = _ask_once(
                    agent,
                    continue_question,
                    show_json=show_json,
                    show_report=show_report,
                    allowed_risk=current_allowed_risk,
                    execute_repair=current_execute_repair,
                    state=state,
                )
                if exit_code == 0:
                    _maybe_prompt_repair_confirmation(
                        agent,
                        state,
                        show_json=show_json,
                        show_report=show_report,
                        allowed_risk=current_allowed_risk,
                        execute_repair=current_execute_repair,
                    )
            continue
        issues_command = _handle_issues_command(question, state)
        if issues_command is not None:
            continue
        exit_code = _ask_once(
            agent,
            question,
            show_json=show_json,
            show_report=show_report,
            allowed_risk=current_allowed_risk,
            execute_repair=current_execute_repair,
            state=state,
        )
        if exit_code == 0:
            _maybe_prompt_repair_confirmation(
                agent,
                state,
                show_json=show_json,
                show_report=show_report,
                allowed_risk=current_allowed_risk,
                execute_repair=current_execute_repair,
            )


def _handle_risk_command(command: str, current_allowed_risk: RiskLevel) -> RiskLevel | None:
    parts = command.split()
    if not parts or parts[0].lower() not in {"/risk", ":risk", "risk"}:
        return None
    if len(parts) == 1:
        print(f"当前允许修复风险等级: {current_allowed_risk}")
        return current_allowed_risk
    requested = parts[1].lower()
    if requested not in RISK_LEVELS:
        print("无效风险等级。可选: none, low, medium, high")
        return current_allowed_risk
    print(f"允许修复风险等级已切换为: {requested}")
    return requested  # type: ignore[return-value]


def _handle_repair_command(command: str, current_execute_repair: bool) -> bool | None:
    parts = command.split()
    if not parts or parts[0].lower() not in {"/repair", ":repair", "repair"}:
        return None
    if len(parts) == 1:
        print(f"当前修复执行模式: {'execute' if current_execute_repair else 'dry-run'}")
        return current_execute_repair
    requested = parts[1].lower()
    if requested in {"on", "execute", "true", "1"}:
        print("修复执行模式已切换为: execute")
        return True
    if requested in {"off", "dry-run", "false", "0"}:
        print("修复执行模式已切换为: dry-run")
        return False
    print("无效修复执行模式。可选: on, off")
    return current_execute_repair


def _maybe_prompt_repair_confirmation(
    agent: NetDocAgent,
    state: ConversationState,
    *,
    show_json: bool,
    show_report: bool,
    allowed_risk: RiskLevel,
    execute_repair: bool,
) -> None:
    prompt = _repair_confirmation_prompt(state)
    if prompt is None:
        return
    phrase = prompt["confirmation_phrase"]

    _print_repair_confirmation_prompt(prompt)
    try:
        answer = input("是否执行该修复？输入 yes 执行，no 拒绝: ").strip().lower()
    except EOFError:
        print()
        return
    if answer not in {"y", "yes", "是", "确认", "执行"}:
        print("已取消本次修复执行。该问题仍保留在 unresolved_issues，可稍后 /continue。")
        return

    print("已确认执行。")
    _ask_once(
        agent,
        state.last_question,
        show_json=show_json,
        show_report=show_report,
        allowed_risk=allowed_risk,
        execute_repair=execute_repair,
        repair_confirmation=phrase,
        state=state,
    )


def _repair_confirmation_prompt(state: ConversationState) -> dict[str, Any] | None:
    result = state.last_result
    if not isinstance(result, dict):
        return None
    repair_observation = result.get("repair_observation")
    if not isinstance(repair_observation, dict):
        return None
    metadata = repair_observation.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get("executed") is True:
        return None
    if metadata.get("execute_requested") is not True:
        return None
    if metadata.get("confirmation_required") is not True:
        return None

    phrase = metadata.get("confirmation_phrase")
    if not isinstance(phrase, str) or not phrase:
        return None
    action = str(metadata.get("action") or "")
    risk = str(metadata.get("action_risk") or "")
    summary = str(repair_observation.get("summary") or "")
    commands = _repair_planned_commands(repair_observation)
    return {
        "action": action,
        "risk": risk,
        "summary": summary,
        "commands": commands,
        "confirmation_phrase": phrase,
    }


def _repair_planned_commands(repair_observation: dict[str, Any]) -> list[Any]:
    commands: list[Any] = []
    checks = repair_observation.get("checks")
    if not isinstance(checks, list):
        return commands
    for check in checks:
        if not isinstance(check, dict):
            continue
        details = check.get("details")
        if not isinstance(details, dict):
            continue
        value = details.get("commands")
        if isinstance(value, list):
            commands.extend(value)
    return commands


def _print_repair_confirmation_prompt(prompt: dict[str, Any]) -> None:
    print("检测到需要确认的风险修复操作：")
    print(f"- 动作: {prompt['action']}")
    print(f"- 风险等级: {prompt['risk']}")
    print(f"- 说明: {prompt['summary']}")
    commands = prompt.get("commands")
    if isinstance(commands, list) and commands:
        print("- 将执行的命令:")
        for command in commands:
            print(f"  {command}")


def _handle_continue_command(command: str, state: ConversationState) -> str | None:
    prefix, separator, extra = command.partition(" ")
    if prefix.lower() not in {"/continue", ":continue", "continue"}:
        return None
    if not state.unresolved_issues:
        print("当前没有未解决项可继续处理。")
        return ""
    question = _build_continue_question(state, extra.strip() if separator else "")
    print(f"继续处理 {len(state.unresolved_issues)} 个未解决项。")
    return question


def _handle_issues_command(command: str, state: ConversationState) -> bool | None:
    parts = command.split()
    if not parts or parts[0].lower() not in {"/issues", ":issues", "issues"}:
        return None
    if len(parts) >= 2 and parts[1].lower() in {"clear", "reset"}:
        state.unresolved_issues.clear()
        state.last_result = None
        print("已清空未解决项。")
        return True
    if not state.unresolved_issues:
        print("当前没有未解决项。")
        return True
    print(json.dumps(state.unresolved_issues, ensure_ascii=False, indent=2))
    return True


def _build_continue_question(state: ConversationState, extra_instruction: str = "") -> str:
    payload = {
        "previous_question": state.last_question,
        "previous_answer": state.last_answer,
        "unresolved_issues": state.unresolved_issues,
    }
    question = (
        "继续处理上一轮 NetDoc 未解决项。请优先针对 unresolved_issues 继续诊断，"
        "不要重复已经解决或已经完成复测的项目。\n"
        f"上一轮上下文 JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    if extra_instruction:
        question += f"\n用户补充说明:\n{extra_instruction}"
    return question


def _update_conversation_state(
    state: ConversationState,
    question: str,
    result: object,
) -> None:
    state.last_question = question
    state.last_answer = str(getattr(result, "answer", ""))
    unresolved = getattr(result, "unresolved_issues", [])
    if isinstance(unresolved, list):
        state.unresolved_issues = [item for item in unresolved if isinstance(item, dict)]
    else:
        state.unresolved_issues = []
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        state.last_result = to_dict()
    else:
        state.last_result = None


def _report_markdown(report_observation: dict[str, object]) -> str:
    metadata = report_observation.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    report = metadata.get("report_markdown")
    if not isinstance(report, str):
        return ""
    return report


def _display_text(result: object, *, show_report: bool) -> str:
    if show_report:
        report_observation = getattr(result, "report_observation", None)
        if isinstance(report_observation, dict):
            report = _report_markdown(report_observation)
            if report:
                return report.rstrip()
    return str(getattr(result, "answer", ""))


if __name__ == "__main__":
    raise SystemExit(main())
