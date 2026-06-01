"""Run the repair_actions skill directly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from netdoc.skills.repair_actions import skill  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or execute a NetDoc repair action.")
    parser.add_argument("action", help="Repair action name, for example flush_dns_cache.")
    parser.add_argument("--execute", action="store_true", help="Actually execute the action.")
    parser.add_argument(
        "--allowed-risk",
        choices=["none", "low", "medium", "high"],
        default="none",
        help="Maximum allowed risk level.",
    )
    parser.add_argument(
        "--confirmation",
        default="",
        help="Required phrase for medium/high execution: EXECUTE <action>.",
    )
    parser.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Action option. Repeatable. Comma-separated VALUE becomes a list.",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-command timeout seconds.")
    args = parser.parse_args()

    observation = skill.run(
        {
            "action": args.action,
            "execute": args.execute,
            "allowed_risk": args.allowed_risk,
            "confirmation": args.confirmation,
            "options": _parse_options(args.option),
            "timeout_seconds": args.timeout,
        }
    )
    print(json.dumps(observation, ensure_ascii=False, indent=2))
    return 0


def _parse_options(items: list[str]) -> dict[str, object]:
    options: dict[str, object] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise SystemExit(f"Invalid --option value: {item!r}. Expected KEY=VALUE.")
        options[key] = value.split(",") if "," in value else value
    return options


if __name__ == "__main__":
    raise SystemExit(main())
