"""Command-line entry point for the LLM-backed NetDoc agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from netdoc.core import AnthropicMessagesClient, LLMError, NetDocAgent, PlanningError  # noqa: E402
from netdoc.skills import create_default_registry  # noqa: E402


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
        )
    return _interactive_loop(agent, show_json=args.show_json, show_report=args.show_report)


def _ask_once(agent: NetDocAgent, question: str, *, show_json: bool, show_report: bool) -> int:
    try:
        result = agent.answer(question)
    except (LLMError, PlanningError, KeyError, ValueError) as exc:
        print(f"诊断失败：{exc}", file=sys.stderr)
        return 1

    if show_report and result.report_observation is not None:
        report = _report_markdown(result.report_observation)
        if report:
            print(report.rstrip())
        else:
            print(result.answer)
    else:
        print(result.answer)
    if show_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _interactive_loop(agent: NetDocAgent, *, show_json: bool, show_report: bool) -> int:
    print("NetDoc 交互模式，输入 exit 或 quit 退出。")
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
        _ask_once(agent, question, show_json=show_json, show_report=show_report)


def _report_markdown(report_observation: dict[str, object]) -> str:
    metadata = report_observation.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    report = metadata.get("report_markdown")
    if not isinstance(report, str):
        return ""
    return report


if __name__ == "__main__":
    raise SystemExit(main())
