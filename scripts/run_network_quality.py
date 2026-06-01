"""Run the network_quality skill and print a JSON observation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from netdoc.skills.network_quality import skill  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NetDoc network quality checks.")
    parser.add_argument(
        "targets",
        nargs="*",
        help="Ping targets to compare, for example 223.5.5.5 8.8.8.8 github.com.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=4,
        help="Number of ping probes sent to each target.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Per-probe timeout in seconds.",
    )
    args = parser.parse_args()

    observation = skill.run(
        {
            "targets": args.targets or ["223.5.5.5", "8.8.8.8", "github.com"],
            "count": args.count,
            "timeout_seconds": args.timeout,
        }
    )
    print(json.dumps(observation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
