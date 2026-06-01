"""Routing diagnosis skill."""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.command import CommandResult, run_command
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation
from netdoc.utils.platform import is_linux, is_wsl, platform_name


SKILL_NAME = "routing_diagnosis"
VPN_INTERFACE_KEYWORDS = (
    "tun",
    "tap",
    "wg",
    "wireguard",
    "tailscale",
    "zt",
    "zerotier",
    "ppp",
    "openvpn",
    "clash",
    "mihomo",
    "utun",
)


ROUTING_DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Diagnose local routing state, including default routes, multiple active "
        "interfaces, VPN virtual adapters, WSL gateway behavior, and abnormal route metrics."
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


def diagnose_routing(timeout_seconds: float = 2.0) -> Observation:
    """Collect route-table evidence and flag common local routing conflicts."""
    snapshot = _collect_routing_snapshot(timeout_seconds)
    interfaces = snapshot["interfaces"]
    routes = snapshot["routes"]
    default_routes = _default_routes(routes)
    active_interfaces = _active_interfaces(interfaces)
    vpn_interfaces = _vpn_interfaces(interfaces)

    checks = [
        _default_route_check(default_routes, snapshot["route_command"]),
        _multiple_interfaces_check(active_interfaces),
        _vpn_virtual_interface_check(vpn_interfaces, routes),
        _wsl_gateway_check(default_routes, timeout_seconds),
        _route_priority_check(default_routes, interfaces),
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
            "active_interfaces": active_interfaces,
            "vpn_interfaces": vpn_interfaces,
            "default_routes": default_routes,
            "route_rules": snapshot["rules"],
        },
    )


def _collect_routing_snapshot(timeout_seconds: float) -> dict[str, Any]:
    if not is_linux():
        empty = CommandResult([], 1, "", f"unsupported platform: {platform_name()}")
        return {
            "interfaces": [],
            "routes": [],
            "rules": [],
            "addr_command": empty,
            "route_command": empty,
            "rule_command": empty,
        }

    addr_items, addr_command = _read_ip_json(["ip", "-j", "addr", "show"], timeout_seconds)
    route_items, route_command = _read_ip_json(
        ["ip", "-j", "route", "show", "table", "main"],
        timeout_seconds,
    )
    rule_command = run_command(["ip", "rule", "show"], timeout=timeout_seconds)
    return {
        "interfaces": _normalize_interfaces(addr_items),
        "routes": _normalize_routes(route_items),
        "rules": _parse_ip_rules(rule_command.stdout),
        "addr_command": addr_command,
        "route_command": route_command,
        "rule_command": rule_command,
    }


def _default_route_check(
    default_routes: list[dict[str, Any]],
    command_result: CommandResult,
) -> CheckResult:
    if command_result.returncode != 0:
        return make_check(
            "default_route",
            False,
            "未能读取主路由表。",
            details={"ip_route": _command_details(command_result)},
        )
    if default_routes:
        labels = ", ".join(_route_label(route) for route in default_routes)
        return make_check(
            "default_route",
            True,
            "发现默认路由: " + labels,
            details={"default_routes": default_routes},
        )
    return make_check(
        "default_route",
        False,
        "主路由表中未发现默认路由。",
        details={"default_routes": []},
    )


def _multiple_interfaces_check(active_interfaces: list[dict[str, Any]]) -> CheckResult:
    if len(active_interfaces) <= 1:
        names = ", ".join(item["name"] for item in active_interfaces) or "无"
        return make_check(
            "multiple_active_interfaces",
            True,
            "当前可用非 loopback 网卡数量正常: " + names,
            details={"active_interfaces": active_interfaces},
        )
    names = ", ".join(item["name"] for item in active_interfaces)
    return make_check(
        "multiple_active_interfaces",
        False,
        "发现多个已启用且带可用 IP 的网卡，可能影响路由选择: " + names,
        details={"active_interfaces": active_interfaces},
    )


def _vpn_virtual_interface_check(
    vpn_interfaces: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> CheckResult:
    vpn_route_devs = sorted(
        {
            str(route["dev"])
            for route in routes
            if route.get("dev") and _looks_like_vpn_name(str(route["dev"]))
        }
    )
    if vpn_interfaces or vpn_route_devs:
        names = sorted({item["name"] for item in vpn_interfaces} | set(vpn_route_devs))
        return make_check(
            "vpn_virtual_interfaces",
            True,
            "发现 VPN/虚拟网卡或相关路由设备: " + ", ".join(names),
            details={"vpn_interfaces": vpn_interfaces, "vpn_route_devs": vpn_route_devs},
        )
    return make_check(
        "vpn_virtual_interfaces",
        True,
        "未发现常见 VPN/虚拟网卡名称。",
        details={"vpn_interfaces": [], "vpn_route_devs": []},
    )


def _wsl_gateway_check(
    default_routes: list[dict[str, Any]],
    timeout_seconds: float,
) -> CheckResult:
    if not is_wsl():
        return make_check(
            "wsl_gateway",
            True,
            "当前不是 WSL 环境，跳过 WSL 网关检查。",
            details={"platform": platform_name()},
        )

    gateway = _select_default_gateway(default_routes)
    resolv_nameservers = _read_resolv_nameservers()
    if not gateway:
        return make_check(
            "wsl_gateway",
            False,
            "WSL 环境中未发现默认网关。",
            details={"gateway": None, "resolv_nameservers": resolv_nameservers},
        )

    started = time.perf_counter()
    ping_result = _ping_gateway(gateway, timeout_seconds)
    if ping_result.returncode == 0:
        return make_check(
            "wsl_gateway",
            True,
            f"WSL 默认网关 {gateway} ping 可达。",
            latency_ms=_elapsed_ms(started),
            details={
                "gateway": gateway,
                "resolv_nameservers": resolv_nameservers,
                "ping": _command_details(ping_result),
            },
        )

    neighbor_result = _gateway_neighbor_check(gateway, timeout_seconds)
    neighbor_reachable = _neighbor_result_has_link_layer_address(neighbor_result.stdout)
    evidence = (
        f"WSL 默认网关 {gateway} ICMP 不回包，但邻居表存在网关 MAC，按二层可达处理。"
        if neighbor_reachable
        else f"WSL 默认网关 {gateway} ping 不可达，邻居表也没有可用二层地址。"
    )
    return make_check(
        "wsl_gateway",
        neighbor_reachable,
        evidence,
        latency_ms=_elapsed_ms(started),
        details={
            "gateway": gateway,
            "resolv_nameservers": resolv_nameservers,
            "ping": _command_details(ping_result),
            "ip_neigh": _command_details(neighbor_result),
        },
    )


def _route_priority_check(
    default_routes: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
) -> CheckResult:
    issues: list[dict[str, Any]] = []
    if len(default_routes) > 1:
        metrics = [_route_metric(route) for route in default_routes]
        lowest = min(metrics)
        lowest_routes = [
            route for route in default_routes if _route_metric(route) == lowest
        ]
        if len(lowest_routes) > 1:
            issues.append(
                {
                    "type": "duplicate_best_default_metric",
                    "metric": lowest,
                    "routes": lowest_routes,
                }
            )
        if any(route.get("metric") is None for route in default_routes):
            issues.append({"type": "missing_default_route_metric", "routes": default_routes})

    interface_by_name = {item["name"]: item for item in interfaces}
    for route in default_routes:
        dev = route.get("dev")
        if not dev:
            continue
        interface = interface_by_name.get(str(dev))
        if interface and not interface["is_enabled"]:
            issues.append({"type": "default_route_dev_not_enabled", "route": route})

    if issues:
        labels = ", ".join(str(issue["type"]) for issue in issues)
        return make_check(
            "route_priority",
            False,
            "发现可能异常的默认路由优先级: " + labels,
            details={"default_routes": default_routes, "issues": issues},
        )

    return make_check(
        "route_priority",
        True,
        "未发现重复最低 metric、缺失 metric 或指向禁用网卡的默认路由。",
        details={"default_routes": default_routes},
    )


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
        addresses = _normalize_addresses(item.get("addr_info", []))
        is_loopback = "LOOPBACK" in flags or name == "lo"
        interfaces.append(
            {
                "name": name,
                "operstate": item.get("operstate"),
                "flags": flags,
                "is_loopback": is_loopback,
                "is_enabled": not is_loopback and "UP" in flags,
                "is_vpn_virtual": _looks_like_vpn_name(name),
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


def _normalize_routes(raw_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for item in raw_routes:
        route = {
            "dst": item.get("dst", "default"),
            "gateway": item.get("gateway"),
            "dev": item.get("dev"),
            "protocol": item.get("protocol"),
            "scope": item.get("scope"),
            "prefsrc": item.get("prefsrc"),
            "metric": item.get("metric"),
            "family": item.get("family"),
        }
        routes.append({key: value for key, value in route.items() if value is not None})
    return routes


def _parse_ip_rules(stdout: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        priority, _, rest = line.partition(":")
        rules.append({"priority": priority.strip(), "rule": rest.strip() or line})
    return rules


def _default_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [route for route in routes if route.get("dst", "default") == "default"]


def _active_interfaces(interfaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in interfaces
        if item["is_enabled"] and any(address["is_usable"] for address in item["addresses"])
    ]


def _vpn_interfaces(interfaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in interfaces if item["is_vpn_virtual"]]


def _select_default_gateway(default_routes: list[dict[str, Any]]) -> str | None:
    routes_with_gateway = [route for route in default_routes if route.get("gateway")]
    if not routes_with_gateway:
        return None
    return str(sorted(routes_with_gateway, key=_route_metric)[0]["gateway"])


def _route_metric(route: dict[str, Any]) -> int:
    value = route.get("metric")
    return value if isinstance(value, int) else 0


def _route_label(route: dict[str, Any]) -> str:
    gateway = route.get("gateway", "on-link")
    dev = route.get("dev", "?")
    metric = route.get("metric")
    suffix = f" metric {metric}" if metric is not None else ""
    return f"{gateway} dev {dev}{suffix}"


def _looks_like_vpn_name(name: str) -> bool:
    normalized = name.casefold()
    return any(keyword in normalized for keyword in VPN_INTERFACE_KEYWORDS)


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


def _read_resolv_nameservers() -> list[str]:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as resolv_conf:
            lines = resolv_conf.readlines()
    except OSError:
        return []

    nameservers: list[str] = []
    for raw_line in lines:
        parts = raw_line.strip().split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            nameservers.append(parts[1])
    return nameservers


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


def _summarize(checks: list[CheckResult]) -> str:
    failed = {check["name"] for check in checks if not check["success"]}
    if "default_route" in failed:
        return "本机缺少默认路由，跨网段和公网访问会失败。"
    if "route_priority" in failed:
        return "默认路由存在，但路由优先级可能冲突，需检查 metric、虚拟网卡或残留 VPN 路由。"
    if "wsl_gateway" in failed:
        return "WSL 默认网关不可用，需检查 Windows 主机网络、WSL NAT 或防火墙。"
    if "multiple_active_interfaces" in failed:
        return "发现多个活跃网卡，当前网络可用但存在路由选择冲突风险。"
    return "默认路由、WSL 网关和路由优先级未发现明显异常。"


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
    skill_schema=ROUTING_DIAGNOSIS_SCHEMA,
    implement_function=diagnose_routing,
)
