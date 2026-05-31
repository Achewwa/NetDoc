"""Service connectivity diagnosis skill."""

from __future__ import annotations

import socket
import ssl
import time
from collections.abc import Iterable
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation


SKILL_NAME = "service_connectivity"
SUPPORTED_PROTOCOLS = ("tcp", "https")


SERVICE_CONNECTIVITY_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": "Diagnose host, TCP port and HTTPS/TLS reachability for a remote service.",
    "parameters": {
        "type": "object",
        "properties": {
            "host": {
                "type": "string",
                "minLength": 1,
                "description": "Target hostname or IP address, for example github.com.",
            },
            "ports": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1, "maximum": 65535},
                "minItems": 1,
                "uniqueItems": True,
                "description": "TCP ports to probe.",
            },
            "protocols": {
                "type": "array",
                "items": {"type": "string", "enum": list(SUPPORTED_PROTOCOLS)},
                "minItems": 1,
                "uniqueItems": True,
                "description": "Protocol-level checks to run.",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 30,
                "description": "Per-check timeout in seconds.",
            },
        },
        "required": ["host"],
        "additionalProperties": False,
    },
}


def diagnose_service_connectivity(
    host: str,
    ports: list[int] | None = None,
    protocols: list[str] | None = None,
    timeout_seconds: float = 3.0,
) -> Observation:
    """Run DNS, TCP and HTTPS/TLS checks for a service target."""
    normalized_ports = ports or [443]
    normalized_protocols = protocols or ["tcp"]

    checks: list[CheckResult] = []
    dns_check = _resolve_host(host, timeout_seconds)
    checks.append(dns_check)
    if not dns_check["success"]:
        return make_observation(
            skill=SKILL_NAME,
            status="abnormal",
            checks=checks,
            summary="DNS 解析失败，后续 TCP/HTTPS 检查无法确认。",
            risk_level="none",
            metadata={"host": host, "ports": normalized_ports, "protocols": normalized_protocols},
        )

    if "tcp" in normalized_protocols:
        for port in normalized_ports:
            checks.append(_check_tcp_connect(host, port, timeout_seconds))

    if "https" in normalized_protocols:
        for port in _https_ports(normalized_ports):
            checks.append(_check_https_tls(host, port, timeout_seconds))

    status = "normal" if all(check["success"] for check in checks) else "abnormal"
    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary=_summarize(checks),
        risk_level="none",
        metadata={"host": host, "ports": normalized_ports, "protocols": normalized_protocols},
    )


def _resolve_host(host: str, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_seconds)
        try:
            addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        finally:
            socket.setdefaulttimeout(previous_timeout)
    except OSError as exc:
        return make_check(
            "dns_resolve",
            False,
            f"{host} DNS resolve failed: {exc}",
            latency_ms=_elapsed_ms(started),
        )

    resolved = sorted({item[4][0] for item in addresses})
    return make_check(
        "dns_resolve",
        True,
        f"{host} resolved to {', '.join(resolved[:3])}",
        latency_ms=_elapsed_ms(started),
        details={"addresses": resolved},
    )


def _check_tcp_connect(host: str, port: int, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return make_check(
                f"tcp_connect_{port}",
                True,
                f"{host}:{port} connected",
                latency_ms=_elapsed_ms(started),
            )
    except OSError as exc:
        return make_check(
            f"tcp_connect_{port}",
            False,
            f"{host}:{port} TCP connect failed: {exc}",
            latency_ms=_elapsed_ms(started),
        )


def _check_https_tls(host: str, port: int, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout_seconds) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=host):
                return make_check(
                    f"https_tls_{port}",
                    True,
                    f"{host}:{port} HTTPS TLS handshake succeeded",
                    latency_ms=_elapsed_ms(started),
                )
    except (OSError, ssl.SSLError) as exc:
        return make_check(
            f"https_tls_{port}",
            False,
            f"{host}:{port} HTTPS TLS handshake failed: {exc}",
            latency_ms=_elapsed_ms(started),
        )


def _https_ports(ports: Iterable[int]) -> list[int]:
    port_list = list(ports)
    preferred = [port for port in port_list if port in {443, 8443}]
    if preferred:
        return preferred
    return port_list[:1]


def _summarize(checks: list[CheckResult]) -> str:
    if all(check["success"] for check in checks):
        return "DNS、TCP/HTTPS 连通性均正常。"

    dns_check = _find_check(checks, "dns_resolve")
    if dns_check and not dns_check["success"]:
        return "DNS 解析失败，无法继续确认目标服务连通性。"

    https_checks = [check for check in checks if check["name"].startswith("https_tls_")]
    ssh_check = _find_check(checks, "tcp_connect_22")
    https_reachable = bool(https_checks) and all(check["success"] for check in https_checks)
    https_failed = bool(https_checks) and any(not check["success"] for check in https_checks)

    if https_reachable and ssh_check and not ssh_check["success"]:
        return "HTTPS 可达，但 SSH 端口不可达。"
    if https_failed and ssh_check and ssh_check["success"]:
        return "SSH 端口可达，但 HTTPS 不可达。"
    if not any(check["success"] for check in checks if check["name"] != "dns_resolve"):
        return "目标服务端口不可达，需优先检查代理/VPN、路由或防火墙。"
    return "部分端口或协议不可达，请查看 checks 中的失败项。"


def _find_check(checks: list[CheckResult], name: str) -> CheckResult | None:
    for check in checks:
        if check["name"] == name:
            return check
    return None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


skill = Skill(
    name=SKILL_NAME,
    skill_schema=SERVICE_CONNECTIVITY_SCHEMA,
    implement_function=diagnose_service_connectivity,
)
