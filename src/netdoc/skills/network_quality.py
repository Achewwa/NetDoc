"""Network quality diagnosis skill."""

from __future__ import annotations

import math
import re
import shutil
from statistics import mean
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.command import CommandResult, run_command
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation
from netdoc.utils.platform import is_windows, platform_name


SKILL_NAME = "network_quality"
DEFAULT_TARGETS = ["223.5.5.5", "8.8.8.8", "github.com"]
GOOD_AVG_LATENCY_MS = 150.0
GOOD_PACKET_LOSS_PERCENT = 0.0


NETWORK_QUALITY_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Measure network quality with ICMP ping latency and packet loss, and compare "
        "multiple targets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Target hostname or IP address, for example github.com.",
                },
                "minItems": 1,
                "maxItems": 8,
                "uniqueItems": True,
                "description": "Hosts or IPs to compare with ping.",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Number of ping probes sent to each target.",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 10,
                "description": "Per-probe timeout in seconds.",
            },
        },
        "additionalProperties": False,
    },
}


def diagnose_network_quality(
    targets: list[str] | None = None,
    count: int = 4,
    timeout_seconds: float = 2.0,
) -> Observation:
    """Run ping-based latency and packet-loss checks for one or more targets."""
    normalized_targets = _normalize_targets(targets)
    checks = [
        _ping_target(target, count=count, timeout_seconds=timeout_seconds)
        for target in normalized_targets
    ]

    if len(normalized_targets) > 1:
        checks.append(_compare_targets(checks))

    target_checks = [check for check in checks if check["name"].startswith("ping_quality_")]
    reachable_checks = [
        check for check in target_checks if check["details"].get("received_count", 0) > 0
    ]
    status = "normal" if target_checks and all(check["success"] for check in target_checks) else "abnormal"

    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary=_summarize(target_checks, reachable_checks),
        risk_level="none",
        metadata={
            "targets": normalized_targets,
            "count": count,
            "timeout_seconds": timeout_seconds,
            "platform": platform_name(),
            **_comparison_metadata(target_checks),
        },
    )


def _normalize_targets(targets: list[str] | None) -> list[str]:
    if not targets:
        return DEFAULT_TARGETS.copy()
    return [target.strip() for target in targets]


def _ping_target(target: str, *, count: int, timeout_seconds: float) -> CheckResult:
    if not target or target.startswith("-"):
        return make_check(
            f"ping_quality_{_safe_check_suffix(target or 'empty')}",
            False,
            "目标地址为空或格式不安全，未执行 ping。",
            details={
                "target": target,
                "sent_count": 0,
                "received_count": 0,
                "packet_loss_percent": 100.0,
            },
        )

    if shutil.which("ping") is None:
        return make_check(
            f"ping_quality_{_safe_check_suffix(target)}",
            False,
            "当前系统找不到 ping 命令，无法检测延迟和丢包。",
            details={
                "target": target,
                "sent_count": 0,
                "received_count": 0,
                "packet_loss_percent": 100.0,
            },
        )

    command = _ping_command(target, count=count, timeout_seconds=timeout_seconds)
    command_timeout = max(2.0, count * timeout_seconds + 2.0)
    result = run_command(command, timeout=command_timeout)
    metrics = _parse_ping_result(result, expected_count=count)
    success = _is_quality_good(metrics)

    return make_check(
        f"ping_quality_{_safe_check_suffix(target)}",
        success,
        _ping_evidence(target, metrics, result),
        latency_ms=_latency_for_check(metrics),
        details={
            "target": target,
            "command": result.command,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            **metrics,
        },
    )


def _ping_command(target: str, *, count: int, timeout_seconds: float) -> list[str]:
    if is_windows():
        return ["ping", "-n", str(count), "-w", str(round(timeout_seconds * 1000)), target]
    return ["ping", "-c", str(count), "-W", str(max(1, math.ceil(timeout_seconds))), target]


def _parse_ping_result(result: CommandResult, *, expected_count: int) -> dict[str, Any]:
    output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    sent, received, loss = _parse_packet_counts(output)
    times = _parse_reply_times(output)
    latency = _parse_latency_summary(output)

    if sent is None:
        sent = expected_count
    if received is None:
        received = len(times) if times else 0
    if loss is None:
        loss = round(((sent - received) / sent) * 100, 2) if sent else 100.0

    if latency["avg_ms"] is None and times:
        latency = {
            "min_ms": round(min(times), 2),
            "avg_ms": round(mean(times), 2),
            "max_ms": round(max(times), 2),
        }

    return {
        "sent_count": sent,
        "received_count": received,
        "packet_loss_percent": loss,
        "min_latency_ms": latency["min_ms"],
        "avg_latency_ms": latency["avg_ms"],
        "max_latency_ms": latency["max_ms"],
    }


def _parse_packet_counts(output: str) -> tuple[int | None, int | None, float | None]:
    linux_match = re.search(
        r"(?P<sent>\d+)\s+packets transmitted,\s+"
        r"(?P<received>\d+)\s+(?:packets\s+)?received,.*?"
        r"(?P<loss>\d+(?:\.\d+)?)%\s+packet loss",
        output,
        re.IGNORECASE | re.DOTALL,
    )
    if linux_match:
        return (
            int(linux_match.group("sent")),
            int(linux_match.group("received")),
            float(linux_match.group("loss")),
        )

    windows_match = re.search(
        r"Sent\s*=\s*(?P<sent>\d+),\s*Received\s*=\s*(?P<received>\d+),"
        r"\s*Lost\s*=\s*\d+\s*\((?P<loss>\d+(?:\.\d+)?)%\s*loss\)",
        output,
        re.IGNORECASE,
    )
    if windows_match:
        return (
            int(windows_match.group("sent")),
            int(windows_match.group("received")),
            float(windows_match.group("loss")),
        )

    return None, None, None


def _parse_reply_times(output: str) -> list[float]:
    matches = re.findall(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", output, re.IGNORECASE)
    return [float(item) for item in matches]


def _parse_latency_summary(output: str) -> dict[str, float | None]:
    unix_match = re.search(
        r"(?:rtt|round-trip)[^=]*=\s*"
        r"(?P<min>\d+(?:\.\d+)?)/(?P<avg>\d+(?:\.\d+)?)/(?P<max>\d+(?:\.\d+)?)",
        output,
        re.IGNORECASE,
    )
    if unix_match:
        return {
            "min_ms": float(unix_match.group("min")),
            "avg_ms": float(unix_match.group("avg")),
            "max_ms": float(unix_match.group("max")),
        }

    windows_match = re.search(
        r"Minimum\s*=\s*(?P<min>\d+(?:\.\d+)?)ms,\s*"
        r"Maximum\s*=\s*(?P<max>\d+(?:\.\d+)?)ms,\s*"
        r"Average\s*=\s*(?P<avg>\d+(?:\.\d+)?)ms",
        output,
        re.IGNORECASE,
    )
    if windows_match:
        return {
            "min_ms": float(windows_match.group("min")),
            "avg_ms": float(windows_match.group("avg")),
            "max_ms": float(windows_match.group("max")),
        }

    return {"min_ms": None, "avg_ms": None, "max_ms": None}


def _is_quality_good(metrics: dict[str, Any]) -> bool:
    avg_latency = metrics.get("avg_latency_ms")
    return (
        metrics["received_count"] > 0
        and metrics["packet_loss_percent"] <= GOOD_PACKET_LOSS_PERCENT
        and avg_latency is not None
        and avg_latency <= GOOD_AVG_LATENCY_MS
    )


def _latency_for_check(metrics: dict[str, Any]) -> int | None:
    avg_latency = metrics.get("avg_latency_ms")
    if avg_latency is None:
        return None
    return max(0, round(avg_latency))


def _ping_evidence(target: str, metrics: dict[str, Any], result: CommandResult) -> str:
    if result.timed_out:
        return f"{target} ping 超时，未能完成 {metrics['sent_count']} 次探测。"
    if result.returncode == 127:
        return f"{target} ping 未执行：{result.stderr.strip()}"
    if metrics["received_count"] == 0:
        return (
            f"{target} ping 全部丢包：发送 {metrics['sent_count']}，"
            f"收到 0，丢包 {metrics['packet_loss_percent']}%。"
        )

    latency = metrics.get("avg_latency_ms")
    latency_text = "未知" if latency is None else f"{latency:.2f} ms"
    return (
        f"{target} ping 平均延迟 {latency_text}，发送 {metrics['sent_count']}，"
        f"收到 {metrics['received_count']}，丢包 {metrics['packet_loss_percent']}%。"
    )


def _compare_targets(target_checks: list[CheckResult]) -> CheckResult:
    ranking = _rank_targets(target_checks)
    if not ranking:
        return make_check(
            "target_comparison",
            False,
            "所有目标 ping 均无响应，无法比较目标质量。",
            details={"ranking": []},
        )

    if all(item["received_count"] == 0 for item in ranking):
        return make_check(
            "target_comparison",
            False,
            "所有目标 ping 均无响应，无法比较目标质量。",
            details={"ranking": ranking},
        )

    best = ranking[0]
    worst = ranking[-1]
    return make_check(
        "target_comparison",
        True,
        f"最优目标 {best['target']}，最差目标 {worst['target']}。",
        details={"ranking": ranking},
    )


def _rank_targets(target_checks: list[CheckResult]) -> list[dict[str, Any]]:
    rows = []
    for check in target_checks:
        details = check["details"]
        rows.append(
            {
                "target": details["target"],
                "packet_loss_percent": details["packet_loss_percent"],
                "avg_latency_ms": details.get("avg_latency_ms"),
                "received_count": details["received_count"],
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            item["received_count"] == 0,
            item["packet_loss_percent"],
            float("inf") if item["avg_latency_ms"] is None else item["avg_latency_ms"],
        ),
    )


def _comparison_metadata(reachable_checks: list[CheckResult]) -> dict[str, Any]:
    ranking = _rank_targets(reachable_checks)
    if not ranking:
        return {}
    return {
        "best_target": ranking[0]["target"],
        "worst_target": ranking[-1]["target"],
    }


def _summarize(target_checks: list[CheckResult], reachable_checks: list[CheckResult]) -> str:
    if not target_checks:
        return "未执行网络质量检测。"
    if not reachable_checks:
        return "所有目标 ping 均无响应，网络质量无法正常确认。"
    if all(check["success"] for check in target_checks):
        return "所有目标 ping 延迟和丢包率均在正常范围内。"

    lost_targets = [
        check["details"]["target"]
        for check in target_checks
        if check["details"].get("packet_loss_percent", 100.0) > GOOD_PACKET_LOSS_PERCENT
    ]
    slow_targets = [
        check["details"]["target"]
        for check in target_checks
        if (check["details"].get("avg_latency_ms") or 0) > GOOD_AVG_LATENCY_MS
    ]
    if lost_targets:
        return f"检测到丢包目标：{', '.join(lost_targets)}。"
    if slow_targets:
        return f"检测到高延迟目标：{', '.join(slow_targets)}。"
    return "部分目标 ping 质量异常，请查看 checks 中的延迟和丢包证据。"


def _safe_check_suffix(target: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", target.strip()).strip("_").lower()
    return suffix or "target"


skill = Skill(
    name=SKILL_NAME,
    skill_schema=NETWORK_QUALITY_SCHEMA,
    implement_function=diagnose_network_quality,
)
