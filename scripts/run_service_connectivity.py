"""Run the service_connectivity skill and print a JSON observation."""

from __future__ import annotations

import argparse
import json

from netdoc.skills.service_connectivity import skill


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NetDoc service connectivity checks.")
    parser.add_argument("host", help="Target host, for example github.com.")
    parser.add_argument(
        "--ports",
        nargs="+",
        type=int,
        default=[443, 22],
        help="TCP ports to check.",
    )
    parser.add_argument(
        "--protocols",
        nargs="+",
        default=["tcp", "https"],
        choices=["tcp", "https"],
        help="Protocols to check.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Per-check timeout in seconds.",
    )
    args = parser.parse_args()

    observation = skill.run(
        {
            "host": args.host,
            "ports": args.ports,
            "protocols": args.protocols,
            "timeout_seconds": args.timeout,
        }
    )
    print(json.dumps(observation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
