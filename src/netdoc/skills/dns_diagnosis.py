"""DNS diagnosis skill."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.command import run_command
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation
from netdoc.utils.platform import is_linux, platform_name


SKILL_NAME = "dns_diagnosis"


DNS_DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Diagnose DNS configuration, domain resolution latency, resolved IPs, "
        "and direct public-IP connectivity comparison."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "minLength": 1,
                "description": "Domain name to resolve, for example github.com.",
            },
            "port": {
                "type": "integer",
                "minimum": 1,
                "maximum": 65535,
                "description": "TCP port used for direct public-IP comparison.",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 30,
                "description": "Per-check timeout in seconds.",
            },
            "use_tls": {
                "type": "boolean",
                "description": "Whether to attempt a TLS handshake during direct IP comparison.",
            },
        },
        "required": ["domain"],
        "additionalProperties": False,
    },
}


def diagnose_dns(
    domain: str,
    port: int = 443,
    timeout_seconds: float = 3.0,
    use_tls: bool = True,
) -> Observation:
    """Run DNS configuration, resolution and direct-IP comparison checks."""
    checks: list[CheckResult] = []
    checks.append(_dns_config_check(timeout_seconds))

    resolution_check = _resolve_domain(domain, timeout_seconds)
    checks.append(resolution_check)
    addresses = _check_addresses(resolution_check)

    if not resolution_check["success"]:
        checks.append(_nslookup_check(domain, timeout_seconds))
        return make_observation(
            skill=SKILL_NAME,
            status="abnormal",
            checks=checks,
            summary="域名解析失败，已记录当前 DNS 配置和 nslookup 辅助结果。",
            risk_level="none",
            metadata={"domain": domain, "port": port, "platform": platform_name()},
        )

    public_ips = _public_ips(addresses)
    checks.append(_public_ip_classification_check(addresses, public_ips))
    checks.append(_direct_public_ip_check(domain, public_ips, port, timeout_seconds, use_tls))

    status = "normal" if all(check["success"] for check in checks) else "abnormal"
    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary=_summarize(checks),
        risk_level="none",
        metadata={
            "domain": domain,
            "port": port,
            "platform": platform_name(),
            "resolved_ips": addresses,
            "public_ips": public_ips,
        },
    )


def _dns_config_check(timeout_seconds: float) -> CheckResult:
    details: dict[str, Any] = {}

    resolv_conf = _read_resolv_conf()
    if resolv_conf is not None:
        details["resolv_conf"] = resolv_conf

    if is_linux():
        result = run_command(["resolvectl", "dns"], timeout=timeout_seconds)
        if result.returncode != 0:
            result = run_command(["resolvectl", "status"], timeout=timeout_seconds)
        if result.returncode == 0 or result.stdout or result.stderr:
            details["resolvectl"] = _command_details(result)

    servers = _extract_nameservers(resolv_conf or "")
    evidence = (
        "当前 DNS 服务器: " + ", ".join(servers)
        if servers
        else "未能从 /etc/resolv.conf 识别 nameserver。"
    )
    return make_check(
        "dns_config",
        bool(servers or details.get("resolvectl")),
        evidence,
        details=details,
    )


def _resolve_domain(domain: str, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_seconds)
        try:
            address_info = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
        finally:
            socket.setdefaulttimeout(previous_timeout)
    except OSError as exc:
        return make_check(
            "dns_resolve",
            False,
            f"{domain} 解析失败: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"domain": domain},
        )

    addresses = sorted({item[4][0] for item in address_info})
    return make_check(
        "dns_resolve",
        bool(addresses),
        f"{domain} 解析到 {', '.join(addresses[:5])}",
        latency_ms=_elapsed_ms(started),
        details={"domain": domain, "addresses": addresses},
    )


def _nslookup_check(domain: str, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    result = run_command(["nslookup", domain], timeout=timeout_seconds)
    success = result.returncode == 0
    evidence = "nslookup 解析成功。" if success else "nslookup 解析失败或命令不可用。"
    return make_check(
        "nslookup",
        success,
        evidence,
        latency_ms=_elapsed_ms(started),
        details=_command_details(result),
    )


def _public_ip_classification_check(addresses: list[str], public_ips: list[str]) -> CheckResult:
    if public_ips:
        return make_check(
            "public_ip_classification",
            True,
            "解析结果包含公网 IP: " + ", ".join(public_ips[:5]),
            details={"addresses": addresses, "public_ips": public_ips},
        )
    return make_check(
        "public_ip_classification",
        False,
        "解析结果中未发现公网 IP，跳过直接公网 IP 访问对比。",
        details={"addresses": addresses, "public_ips": []},
    )


def _direct_public_ip_check(
    domain: str,
    public_ips: list[str],
    port: int,
    timeout_seconds: float,
    use_tls: bool,
) -> CheckResult:
    if not public_ips:
        return make_check(
            f"direct_public_ip_{port}",
            False,
            "没有可用于直连对比的公网 IP。",
            details={"attempts": []},
        )

    attempts: list[dict[str, Any]] = []
    for ip_address in public_ips[:3]:
        started = time.perf_counter()
        try:
            with socket.create_connection((ip_address, port), timeout=timeout_seconds) as raw_socket:
                if use_tls:
                    context = ssl.create_default_context()
                    with context.wrap_socket(raw_socket, server_hostname=domain):
                        pass
            attempts.append(
                {
                    "ip": ip_address,
                    "success": True,
                    "latency_ms": _elapsed_ms(started),
                    "mode": "tls" if use_tls else "tcp",
                }
            )
            return make_check(
                f"direct_public_ip_{port}",
                True,
                f"绕过 DNS 直接访问公网 IP {ip_address}:{port} 成功。",
                latency_ms=attempts[-1]["latency_ms"],
                details={"attempts": attempts},
            )
        except (OSError, ssl.SSLError) as exc:
            attempts.append(
                {
                    "ip": ip_address,
                    "success": False,
                    "latency_ms": _elapsed_ms(started),
                    "mode": "tls" if use_tls else "tcp",
                    "error": str(exc),
                }
            )

    return make_check(
        f"direct_public_ip_{port}",
        False,
        f"绕过 DNS 直接访问公网 IP 的 {port} 端口失败。",
        details={"attempts": attempts},
    )


def _summarize(checks: list[CheckResult]) -> str:
    resolve_check = _find_check(checks, "dns_resolve")
    if resolve_check and not resolve_check["success"]:
        return "域名无法解析，优先检查 DNS 服务器、网络出口或本机解析配置。"

    direct_checks = [check for check in checks if check["name"].startswith("direct_public_ip_")]
    direct_failed = bool(direct_checks) and not any(check["success"] for check in direct_checks)
    if direct_failed:
        return "域名可以解析，但直接访问解析出的公网 IP 失败，问题更可能在网络连通性、代理/VPN 或目标服务。"

    config_check = _find_check(checks, "dns_config")
    if config_check and not config_check["success"]:
        return "域名解析和公网 IP 访问正常，但当前 DNS 配置读取不完整。"

    return "DNS 配置可读取，域名解析和直接公网 IP 访问对比均正常。"


def _read_resolv_conf() -> str | None:
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as config_file:
            return config_file.read()
    except OSError:
        result = run_command(["cat", "/etc/resolv.conf"], timeout=2)
        if result.returncode == 0:
            return result.stdout
    return None


def _extract_nameservers(resolv_conf: str) -> list[str]:
    servers: list[str] = []
    for raw_line in resolv_conf.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
    return servers


def _check_addresses(check: CheckResult) -> list[str]:
    details = check.get("details", {})
    addresses = details.get("addresses") if isinstance(details, dict) else None
    if not isinstance(addresses, list):
        return []
    return [address for address in addresses if isinstance(address, str)]


def _public_ips(addresses: list[str]) -> list[str]:
    public: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_global:
            public.append(str(parsed))
    return public


def _find_check(checks: list[CheckResult], name: str) -> CheckResult | None:
    for check in checks:
        if check["name"] == name:
            return check
    return None


def _command_details(result: Any) -> dict[str, Any]:
    details = result.to_dict()
    details["stdout"] = _truncate(details.get("stdout", ""))
    details["stderr"] = _truncate(details.get("stderr", ""))
    return details


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


skill = Skill(
    name=SKILL_NAME,
    skill_schema=DNS_DIAGNOSIS_SCHEMA,
    implement_function=diagnose_dns,
)
