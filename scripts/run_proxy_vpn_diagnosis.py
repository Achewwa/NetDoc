"""Run the proxy_vpn_diagnosis skill and print a JSON observation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if SRC_PATH.exists():
    sys.path.insert(0, str(SRC_PATH))

from netdoc.skills.proxy_vpn_diagnosis import skill  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NetDoc proxy/VPN diagnosis checks.")
    parser.add_argument(
        "--target-url",
        default="https://github.com",
        help="URL used to test access through the configured proxy.",
    )
    parser.add_argument(
        "--ports",
        nargs="+",
        type=int,
        default=[7890, 7897, 1080, 8080],
        help="Common local proxy ports to check.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Per-command or per-network-check timeout in seconds.",
    )
    args = parser.parse_args()

    observation = skill.run(
        {
            "target_url": args.target_url,
            "common_proxy_ports": args.ports,
            "timeout_seconds": args.timeout,
        }
    )
    print(json.dumps(observation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
