"""Run the dns_diagnosis skill and print a JSON observation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from netdoc.skills.dns_diagnosis import skill  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NetDoc DNS diagnosis checks.")
    parser.add_argument("domain", help="Domain to resolve, for example github.com.")
    parser.add_argument(
        "--port",
        type=int,
        default=443,
        help="TCP port used for direct public-IP comparison.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Per-check timeout in seconds.",
    )
    parser.add_argument(
        "--no-tls",
        action="store_true",
        help="Only check TCP connection when comparing direct public-IP access.",
    )
    args = parser.parse_args()

    observation = skill.run(
        {
            "domain": args.domain,
            "port": args.port,
            "timeout_seconds": args.timeout,
            "use_tls": not args.no_tls,
        }
    )
    print(json.dumps(observation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
