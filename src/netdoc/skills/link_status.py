"""Local link status diagnosis skill."""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.command import CommandResult, run_command
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation
from netdoc.utils.platform import is_linux, platform_name


SKILL_NAME = "link_status"


LINK_STATUS_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Check whether local network adapters are enabled, IP addresses are usable, "
        "a default gateway exists, and the gateway is reachable."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "timeout_seconds": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 30,
                "description": "Per-command or per-network-check timeout in seconds.",
            }
        },
        "additionalProperties": False,
    },
}


def diagnose_link_status(timeout_seconds: float = 2.0) -> Observation:
    """Collect local link evidence and summarize basic gateway reachability."""
    snapshot = _collect_link_snapshot(timeout_seconds)
    interfaces = snapshot["interfaces"]
    default_routes = snapshot["default_routes"]

    checks = [
        _adapter_enabled_check(interfaces, snapshot["addr_command"]),
        _ip_address_valid_check(interfaces, snapshot["addr_command"]),
        _default_gateway_check(default_routes, snapshot["route_command"]),
        _gateway_reachable_check(_select_default_gateway(default_routes), timeout_seconds),
    ]
    status = "normal" if all(check["success"] for check in checks) else "abnormal"

    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary=_summarize(checks),
        risk_level="none",
        metadata={
            "platform": platform_name(),
            "interfaces": interfaces,
            "default_routes": default_routes,
        },
    )


def _collect_link_snapshot(timeout_seconds: float) -> dict[str, Any]:
    if not is_linux():
        empty = CommandResult([], 1, "", f"unsupported platform: {platform_name()}")
        return {"interfaces": [], "default_routes": [], "addr_command": empty, "route_command": empty}

    addr_items, addr_command = _read_ip_json(["ip", "-j", "addr", "show"], timeout_seconds)
    route_items, route_command = _read_ip_json(
        ["ip", "-j", "route", "show", "default"],
        timeout_seconds,
    )
    return {
        "interfaces": _normalize_interfaces(addr_items),
        "default_routes": _normalize_default_routes(route_items),
        "addr_command": addr_command,
        "route_command": route_command,
    }


def _adapter_enabled_check(
    interfaces: list[dict[str, Any]],
    command_result: CommandResult,
) -> CheckResult:
    if command_result.returncode != 0:
        return make_check(
            "adapter_enabled",
            False,
            "未能读取网卡状态。",
            details={"ip_addr": _command_details(command_result)},
        )

    enabled = [item for item in interfaces if item["is_enabled"]]
    if enabled:
        names = ", ".join(item["name"] for item in enabled)
        return make_check(
            "adapter_enabled",
            True,
            "发现已启用网卡: " + names,
            details={"interfaces": interfaces},
        )

    return make_check(
        "adapter_enabled",
        False,
        "未发现已启用的非 loopback 网卡。",
        details={"interfaces": interfaces},
    )


def _ip_address_valid_check(
    interfaces: list[dict[str, Any]],
    command_result: CommandResult,
) -> CheckResult:
    if command_result.returncode != 0:
        return make_check(
            "ip_address_valid",
            False,
            "未能读取本机 IP 地址。",
            details={"ip_addr": _command_details(command_result)},
        )

    valid_addresses = [
        address
        for item in interfaces
        if item["is_enabled"]
        for address in item["addresses"]
        if address["is_usable"]
    ]
    if valid_addresses:
        labels = ", ".join(address["address"] for address in valid_addresses[:5])
        return make_check(
            "ip_address_valid",
            True,
            "已启用网卡存在可用 IP 地址: " + labels,
            details={"interfaces": interfaces, "valid_addresses": valid_addresses},
        )

    return make_check(
        "ip_address_valid",
        False,
        "已启用网卡没有可用于正常联网的 IP 地址。",
        details={"interfaces": interfaces},
    )


def _default_gateway_check(
    default_routes: list[dict[str, Any]],
    command_result: CommandResult,
) -> CheckResult:
    if command_result.returncode != 0:
        return make_check(
            "default_gateway_exists",
            False,
            "未能读取默认网关。",
            details={"ip_route": _command_details(command_result)},
        )

    gateways = [route for route in default_routes if route.get("gateway")]
    if gateways:
        labels = ", ".join(f"{route['gateway']} dev {route.get('dev', '?')}" for route in gateways)
        return make_check(
            "default_gateway_exists",
            True,
            "发现默认网关: " + labels,
            details={"default_routes": default_routes},
        )

    return make_check(
        "default_gateway_exists",
        False,
        "未发现带 gateway 的默认路由。",
        details={"default_routes": default_routes},
    )


def _gateway_reachable_check(gateway: str | None, timeout_seconds: float) -> CheckResult:
    if not gateway:
        return make_check(
            "gateway_reachable",
            False,
            "没有默认网关，无法探测网关可达性。",
            details={"gateway": None},
        )

    started = time.perf_counter()
    result = _ping_gateway(gateway, timeout_seconds)
    if result.returncode == 0:
        return make_check(
            "gateway_reachable",
            True,
            f"默认网关 {gateway} ping 可达。",
            latency_ms=_elapsed_ms(started),
            details={"gateway": gateway, "ping": _command_details(result)},
        )

    neighbor_result = _gateway_neighbor_check(gateway, timeout_seconds)
    neighbor_reachable = _neighbor_result_has_link_layer_address(neighbor_result.stdout)
    evidence = (
        f"默认网关 {gateway} ICMP 不回包，但邻居表存在网关 MAC，按二层可达处理。"
        if neighbor_reachable
        else f"默认网关 {gateway} ping 不可达，邻居表也没有可用二层地址。"
    )
    return make_check(
        "gateway_reachable",
        neighbor_reachable,
        evidence,
        latency_ms=_elapsed_ms(started),
        details={
            "gateway": gateway,
            "ping": _command_details(result),
            "ip_neigh": _command_details(neighbor_result),
        },
    )


def _ping_gateway(gateway: str, timeout_seconds: float) -> CommandResult:
    timeout = max(1, int(round(timeout_seconds)))
    command = ["ping", "-c", "1", "-W", str(timeout), gateway]
    try:
        parsed = ipaddress.ip_address(gateway)
    except ValueError:
        parsed = None
    if parsed and parsed.version == 6:
        command = ["ping", "-6", "-c", "1", "-W", str(timeout), gateway]
    return run_command(command, timeout=timeout_seconds + 1)


def _gateway_neighbor_check(gateway: str, timeout_seconds: float) -> CommandResult:
    return run_command(["ip", "neigh", "show", gateway], timeout=timeout_seconds)


def _neighbor_result_has_link_layer_address(stdout: str) -> bool:
    normalized = stdout.casefold()
    if "failed" in normalized or "incomplete" in normalized:
        return False
    return "lladdr" in normalized


def _read_ip_json(command: list[str], timeout_seconds: float) -> tuple[list[dict[str, Any]], CommandResult]:
    result = run_command(command, timeout=timeout_seconds)
    if result.returncode != 0:
        return [], result
    try:
        decoded = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], CommandResult(command, 1, result.stdout, f"invalid JSON from ip command: {exc}")
    if not isinstance(decoded, list):
        return [], CommandResult(command, 1, result.stdout, "unexpected JSON shape from ip command")
    return [item for item in decoded if isinstance(item, dict)], result


def _normalize_interfaces(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    interfaces: list[dict[str, Any]] = []
    for item in raw_items:
        name = str(item.get("ifname", ""))
        flags = [str(flag) for flag in item.get("flags", []) if isinstance(flag, str)]
        is_loopback = "LOOPBACK" in flags or name == "lo"
        addresses = _normalize_addresses(item.get("addr_info", []))
        interfaces.append(
            {
                "name": name,
                "operstate": item.get("operstate"),
                "flags": flags,
                "is_loopback": is_loopback,
                "is_enabled": not is_loopback and "UP" in flags,
                "addresses": addresses,
            }
        )
    return interfaces


def _normalize_addresses(raw_addresses: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_addresses, list):
        return []
    addresses: list[dict[str, Any]] = []
    for item in raw_addresses:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family", ""))
        local = str(item.get("local", ""))
        if family not in {"inet", "inet6"} or not local:
            continue
        addresses.append(
            {
                "family": family,
                "address": local,
                "prefixlen": item.get("prefixlen"),
                "scope": item.get("scope"),
                "is_usable": _is_usable_ip(local),
            }
        )
    return addresses


def _normalize_default_routes(raw_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for item in raw_routes:
        if str(item.get("dst", "default")) != "default":
            continue
        route = {
            "gateway": item.get("gateway"),
            "dev": item.get("dev"),
            "protocol": item.get("protocol"),
            "metric": item.get("metric"),
            "family": item.get("family"),
            "prefsrc": item.get("prefsrc"),
        }
        routes.append({key: value for key, value in route.items() if value is not None})
    return routes


def _select_default_gateway(default_routes: list[dict[str, Any]]) -> str | None:
    routes_with_gateway = [route for route in default_routes if route.get("gateway")]
    if not routes_with_gateway:
        return None

    def metric(route: dict[str, Any]) -> int:
        value = route.get("metric")
        return value if isinstance(value, int) else 0

    return str(sorted(routes_with_gateway, key=metric)[0]["gateway"])


def _is_usable_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    )


def _summarize(checks: list[CheckResult]) -> str:
    failed = {check["name"] for check in checks if not check["success"]}
    if "adapter_enabled" in failed:
        return "未发现已启用网卡，链路层状态异常。"
    if "ip_address_valid" in failed:
        return "网卡已启用但没有可用 IP 地址，优先检查 DHCP、静态 IP 或虚拟网卡配置。"
    if "default_gateway_exists" in failed:
        return "本机缺少默认网关，无法正常访问跨网段或公网目标。"
    if "gateway_reachable" in failed:
        return "默认网关存在但不可达，优先检查本地链路、网关、防火墙或 WSL NAT。"
    return "网卡启用、IP 地址、默认网关和网关可达性均正常。"


def _command_details(result: CommandResult) -> dict[str, Any]:
    details = result.to_dict()
    details["stdout"] = _truncate(str(details.get("stdout", "")))
    details["stderr"] = _truncate(str(details.get("stderr", "")))
    return details


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


skill = Skill(
    name=SKILL_NAME,
    skill_schema=LINK_STATUS_SCHEMA,
    implement_function=diagnose_link_status,
)
