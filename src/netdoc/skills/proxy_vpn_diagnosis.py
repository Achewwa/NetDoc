"""Proxy and VPN diagnosis skill."""

from __future__ import annotations

import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.command import CommandResult, run_command
from netdoc.utils.json_types import CheckResult, Observation, make_check, make_observation
from netdoc.utils.platform import is_linux, is_windows, is_wsl, platform_name


SKILL_NAME = "proxy_vpn_diagnosis"
DEFAULT_PROXY_PORTS = [7890, 7897, 1080, 8080]
PROXY_ENV_NAMES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
PROCESS_KEYWORDS = (
    "clash",
    "mihomo",
    "v2ray",
    "xray",
    "sing-box",
    "shadowsocks",
    "openvpn",
    "wireguard",
    "tailscale",
    "zerotier",
    "anyconnect",
    "globalprotect",
    "outline",
    "hysteria",
)


PROXY_VPN_DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Diagnose proxy and VPN state, including proxy environment variables, Git proxy "
        "configuration, system proxy settings, common local proxy ports, GitHub access "
        "through a configured proxy, and Clash/VPN related processes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_url": {
                "type": "string",
                "minLength": 1,
                "description": "URL used to test access through the configured proxy.",
            },
            "common_proxy_ports": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1, "maximum": 65535},
                "minItems": 1,
                "uniqueItems": True,
                "description": "Local proxy ports to check for listeners.",
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 30,
                "description": "Per-command or per-network-check timeout in seconds.",
            },
        },
        "additionalProperties": False,
    },
}


def diagnose_proxy_vpn(
    target_url: str = "https://github.com",
    common_proxy_ports: list[int] | None = None,
    timeout_seconds: float = 3.0,
) -> Observation:
    """Collect proxy/VPN evidence and flag stale proxy configurations."""
    ports = common_proxy_ports or DEFAULT_PROXY_PORTS
    checks: list[CheckResult] = []

    env_check, env_endpoints = _environment_proxy_check()
    checks.append(env_check)

    git_check, git_endpoints = _git_proxy_check(timeout_seconds)
    checks.append(git_check)

    system_check, system_endpoints = _system_proxy_check(timeout_seconds)
    checks.append(system_check)

    configured_endpoints = env_endpoints + git_endpoints + system_endpoints
    port_check = _common_proxy_ports_check(ports, configured_endpoints, timeout_seconds)
    checks.append(port_check)

    github_check = _github_via_proxy_check(target_url, configured_endpoints, timeout_seconds)
    checks.append(github_check)

    process_check = _proxy_vpn_process_check(timeout_seconds)
    checks.append(process_check)

    status = _status_from_checks(checks, configured_endpoints)
    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary=_summarize(checks, configured_endpoints),
        risk_level="none",
        metadata={
            "platform": platform_name(),
            "target_url": target_url,
            "common_proxy_ports": ports,
            "configured_proxy_endpoints": configured_endpoints,
        },
    )


def _environment_proxy_check() -> tuple[CheckResult, list[dict[str, Any]]]:
    values = {
        name: os.environ[name]
        for name in PROXY_ENV_NAMES
        if name in os.environ and os.environ[name].strip()
    }
    endpoints, malformed = _parse_proxy_values(values, source="environment")

    if malformed:
        return (
            make_check(
                "proxy_environment_variables",
                False,
                "发现代理环境变量，但部分值无法解析。",
                details={"values": values, "endpoints": endpoints, "malformed": malformed},
            ),
            endpoints,
        )
    if endpoints:
        labels = ", ".join(f"{item['name']}={item['url']}" for item in endpoints)
        return (
            make_check(
                "proxy_environment_variables",
                True,
                "发现代理环境变量: " + labels,
                details={"values": values, "endpoints": endpoints},
            ),
            endpoints,
        )
    return (
        make_check(
            "proxy_environment_variables",
            True,
            "未发现 http_proxy / https_proxy 代理环境变量。",
            details={"values": {}},
        ),
        [],
    )


def _git_proxy_check(timeout_seconds: float) -> tuple[CheckResult, list[dict[str, Any]]]:
    entries: dict[str, str] = {}
    command_details: dict[str, Any] = {}
    for scope, command in (
        ("local", ["git", "config", "--get-regexp", r".*proxy.*"]),
        ("global", ["git", "config", "--global", "--get-regexp", r".*proxy.*"]),
    ):
        result = run_command(command, timeout=timeout_seconds)
        command_details[scope] = _command_details(result)
        if result.returncode == 0:
            entries.update(_parse_git_config_lines(result.stdout, scope))

    endpoints, malformed = _parse_proxy_values(entries, source="git")
    if malformed:
        return (
            make_check(
                "git_proxy_config",
                False,
                "发现 Git proxy 配置，但部分值无法解析。",
                details={"entries": entries, "endpoints": endpoints, "malformed": malformed}
                | command_details,
            ),
            endpoints,
        )
    if endpoints:
        labels = ", ".join(f"{item['name']}={item['url']}" for item in endpoints)
        return (
            make_check(
                "git_proxy_config",
                True,
                "发现 Git proxy 配置: " + labels,
                details={"entries": entries, "endpoints": endpoints} | command_details,
            ),
            endpoints,
        )
    return (
        make_check(
            "git_proxy_config",
            True,
            "未发现 Git proxy 配置。",
            details={"entries": entries} | command_details,
        ),
        [],
    )


def _system_proxy_check(timeout_seconds: float) -> tuple[CheckResult, list[dict[str, Any]]]:
    if is_windows():
        return _windows_system_proxy_check(timeout_seconds)
    if is_wsl():
        return _wsl_system_proxy_check(timeout_seconds)
    if is_linux():
        return _linux_system_proxy_check(timeout_seconds)
    if platform_name() == "darwin":
        return _macos_system_proxy_check(timeout_seconds)
    return (
        make_check(
            "system_proxy_config",
            True,
            f"当前平台 {platform_name()} 暂未实现系统代理读取。",
            details={"platform": platform_name()},
        ),
        [],
    )


def _linux_system_proxy_check(timeout_seconds: float) -> tuple[CheckResult, list[dict[str, Any]]]:
    mode_result = run_command(["gsettings", "get", "org.gnome.system.proxy", "mode"], timeout=2)
    details: dict[str, Any] = {"gsettings_mode": _command_details(mode_result)}
    if mode_result.returncode != 0:
        return (
            make_check(
                "system_proxy_config",
                True,
                "未能通过 gsettings 读取 Linux 桌面系统代理，可能不是 GNOME 环境。",
                details=details,
            ),
            [],
        )

    values: dict[str, str] = {}
    mode = _clean_setting_value(mode_result.stdout)
    details["mode"] = mode
    if mode == "manual":
        for schema, protocol in (
            ("org.gnome.system.proxy.http", "http"),
            ("org.gnome.system.proxy.https", "https"),
            ("org.gnome.system.proxy.socks", "socks"),
        ):
            host = _gsettings(schema, "host", timeout_seconds, details)
            port = _gsettings(schema, "port", timeout_seconds, details)
            if host and port:
                scheme = "socks5" if protocol == "socks" else protocol
                values[f"system.{protocol}"] = f"{scheme}://{host}:{port}"

    endpoints, malformed = _parse_proxy_values(values, source="system")
    return _system_proxy_result(values, endpoints, malformed, details)


def _wsl_system_proxy_check(timeout_seconds: float) -> tuple[CheckResult, list[dict[str, Any]]]:
    command = (
        "$p=Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet "
        "Settings'; "
        "\"ProxyEnable=$($p.ProxyEnable)\"; "
        "\"ProxyServer=$($p.ProxyServer)\"; "
        "\"AutoConfigURL=$($p.AutoConfigURL)\""
    )
    result = run_command(
        ["powershell.exe", "-NoProfile", "-Command", command],
        timeout=timeout_seconds,
    )
    details = {"windows_internet_settings": _command_details(result)}
    if result.returncode != 0:
        return (
            make_check(
                "system_proxy_config",
                True,
                "WSL 中未能读取 Windows 系统代理配置。",
                details=details,
            ),
            [],
        )

    values = _parse_windows_proxy_stdout(result.stdout)
    endpoints, malformed = _parse_proxy_values(values, source="system")
    for endpoint in endpoints:
        if _is_local_host(str(endpoint.get("host", ""))):
            endpoint["diagnostic_scope"] = "windows_host"
    return _system_proxy_result(values, endpoints, malformed, details)


def _windows_system_proxy_check(timeout_seconds: float) -> tuple[CheckResult, list[dict[str, Any]]]:
    command = (
        "$p=Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet "
        "Settings'; "
        "\"ProxyEnable=$($p.ProxyEnable)\"; "
        "\"ProxyServer=$($p.ProxyServer)\"; "
        "\"AutoConfigURL=$($p.AutoConfigURL)\""
    )
    result = run_command(["powershell", "-NoProfile", "-Command", command], timeout=timeout_seconds)
    details = {"windows_internet_settings": _command_details(result)}
    if result.returncode != 0:
        return (
            make_check(
                "system_proxy_config",
                True,
                "未能读取 Windows 系统代理配置。",
                details=details,
            ),
            [],
        )

    values = _parse_windows_proxy_stdout(result.stdout)
    endpoints, malformed = _parse_proxy_values(values, source="system")
    return _system_proxy_result(values, endpoints, malformed, details)


def _macos_system_proxy_check(timeout_seconds: float) -> tuple[CheckResult, list[dict[str, Any]]]:
    result = run_command(["scutil", "--proxy"], timeout=timeout_seconds)
    details = {"scutil_proxy": _command_details(result)}
    if result.returncode != 0:
        return (
            make_check("system_proxy_config", True, "未能读取 macOS 系统代理配置。", details=details),
            [],
        )

    values = _parse_scutil_proxy_stdout(result.stdout)
    endpoints, malformed = _parse_proxy_values(values, source="system")
    return _system_proxy_result(values, endpoints, malformed, details)


def _system_proxy_result(
    values: dict[str, str],
    endpoints: list[dict[str, Any]],
    malformed: list[dict[str, str]],
    details: dict[str, Any],
) -> tuple[CheckResult, list[dict[str, Any]]]:
    if malformed:
        return (
            make_check(
                "system_proxy_config",
                False,
                "发现系统代理配置，但部分值无法解析。",
                details={"values": values, "endpoints": endpoints, "malformed": malformed}
                | details,
            ),
            endpoints,
        )
    if endpoints:
        labels = ", ".join(f"{item['name']}={item['url']}" for item in endpoints)
        return (
            make_check(
                "system_proxy_config",
                True,
                "发现系统代理配置: " + labels,
                details={"values": values, "endpoints": endpoints} | details,
            ),
            endpoints,
        )
    return (
        make_check(
            "system_proxy_config",
            True,
            "未发现已启用的系统代理配置。",
            details={"values": values} | details,
        ),
        [],
    )


def _common_proxy_ports_check(
    common_ports: list[int],
    endpoints: list[dict[str, Any]],
    timeout_seconds: float,
) -> CheckResult:
    probed_local_endpoints = [
        item
        for item in endpoints
        if isinstance(item.get("port"), int) and _should_probe_as_local_port(item)
    ]
    skipped_local_endpoints = [
        item
        for item in endpoints
        if isinstance(item.get("port"), int)
        and _is_local_host(str(item.get("host", "")))
        and not _should_probe_as_local_port(item)
    ]
    configured_local_ports = sorted(
        {int(item["port"]) for item in probed_local_endpoints}
    )
    ports = sorted(set(common_ports) | set(configured_local_ports))
    results = {
        str(port): _port_probe_result(port, min(timeout_seconds, 1.0))
        for port in ports
        if 1 <= port <= 65535
    }
    stale_ports = [
        port for port in configured_local_ports if not results.get(str(port), {}).get("listening")
    ]

    if stale_ports:
        return make_check(
            "common_proxy_ports",
            False,
            "代理配置指向本机端口，但端口未监听: " + ", ".join(map(str, stale_ports)),
            details={
                "ports": results,
                "configured_local_ports": configured_local_ports,
                "skipped_local_endpoints": skipped_local_endpoints,
            },
        )

    listening = [port for port, result in results.items() if result.get("listening")]
    if listening:
        return make_check(
            "common_proxy_ports",
            True,
            "发现常见代理端口正在监听: " + ", ".join(listening),
            details={
                "ports": results,
                "configured_local_ports": configured_local_ports,
                "skipped_local_endpoints": skipped_local_endpoints,
            },
        )

    if skipped_local_endpoints:
        return make_check(
            "common_proxy_ports",
            True,
            "常见 WSL 本机代理端口未监听；Windows 系统代理 localhost 端口不在 WSL 内直接判定。",
            details={
                "ports": results,
                "configured_local_ports": configured_local_ports,
                "skipped_local_endpoints": skipped_local_endpoints,
            },
        )

    return make_check(
        "common_proxy_ports",
        True,
        "常见代理端口未监听；若没有启用本机代理，这属于正常状态。",
        details={
            "ports": results,
            "configured_local_ports": configured_local_ports,
            "skipped_local_endpoints": skipped_local_endpoints,
        },
    )


def _github_via_proxy_check(
    target_url: str,
    endpoints: list[dict[str, Any]],
    timeout_seconds: float,
) -> CheckResult:
    endpoint = _select_proxy_endpoint(endpoints)
    if endpoint is None:
        return make_check(
            "github_via_proxy",
            True,
            "未发现可用于测试的代理配置，跳过通过代理访问 GitHub。",
            details={"target_url": target_url},
        )

    proxy_url = str(endpoint["url"])
    curl_result = run_command(
        [
            "curl",
            "--head",
            "--location",
            "--silent",
            "--show-error",
            "--max-time",
            str(max(1, int(timeout_seconds))),
            "--proxy",
            proxy_url,
            target_url,
        ],
        timeout=timeout_seconds + 1,
    )
    if curl_result.returncode == 0:
        return make_check(
            "github_via_proxy",
            True,
            f"通过代理 {proxy_url} 访问 {target_url} 成功。",
            details={"target_url": target_url, "proxy": endpoint, "curl": _command_details(curl_result)},
        )
    if curl_result.returncode != 127:
        return make_check(
            "github_via_proxy",
            False,
            f"通过代理 {proxy_url} 访问 {target_url} 失败。",
            details={"target_url": target_url, "proxy": endpoint, "curl": _command_details(curl_result)},
        )

    if not proxy_url.startswith(("http://", "https://")):
        return make_check(
            "github_via_proxy",
            False,
            f"当前环境缺少 curl，无法用标准库测试非 HTTP 代理 {proxy_url}。",
            details={"target_url": target_url, "proxy": endpoint, "curl": _command_details(curl_result)},
        )

    started = time.perf_counter()
    try:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        request = urllib.request.Request(target_url, method="HEAD")
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", 0)
        return make_check(
            "github_via_proxy",
            200 <= int(status_code) < 500,
            f"通过代理 {proxy_url} 访问 {target_url} 返回 HTTP {status_code}。",
            latency_ms=_elapsed_ms(started),
            details={"target_url": target_url, "proxy": endpoint, "status_code": status_code},
        )
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return make_check(
            "github_via_proxy",
            False,
            f"通过代理 {proxy_url} 访问 {target_url} 失败: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"target_url": target_url, "proxy": endpoint, "error": str(exc)},
        )


def _proxy_vpn_process_check(timeout_seconds: float) -> CheckResult:
    if is_windows():
        result = run_command(["tasklist"], timeout=timeout_seconds)
    else:
        result = run_command(["ps", "-eo", "pid,comm,args"], timeout=timeout_seconds)

    matches = _find_process_matches(result.stdout)
    if matches:
        names = ", ".join(item["keyword"] for item in matches[:5])
        return make_check(
            "clash_vpn_processes",
            True,
            "发现 Clash/VPN 相关进程: " + names,
            details={"matches": matches, "process_command": _command_details(result)},
        )
    if result.returncode != 0:
        return make_check(
            "clash_vpn_processes",
            True,
            "未能读取进程列表，无法确认 Clash/VPN 进程。",
            details={"process_command": _command_details(result)},
        )
    return make_check(
        "clash_vpn_processes",
        True,
        "未发现 Clash/VPN 相关进程。",
        details={"matches": [], "process_command": _command_details(result)},
    )


def _parse_proxy_values(
    values: dict[str, str],
    *,
    source: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    endpoints: list[dict[str, Any]] = []
    malformed: list[dict[str, str]] = []
    for name, value in values.items():
        if "autoconfig" in name.casefold():
            continue
        parsed = _parse_proxy_value(value, source=source, name=name)
        if parsed is None:
            continue
        if "error" in parsed:
            malformed.append({"name": name, "value": value, "error": str(parsed["error"])})
            continue
        endpoints.append(parsed)
    return endpoints, malformed


def _parse_proxy_value(value: str, *, source: str, name: str) -> dict[str, Any] | None:
    cleaned = value.strip().strip("'\"")
    if not cleaned or cleaned.casefold() in {"none", "off", "direct"}:
        return None
    candidate = cleaned if "://" in cleaned else f"http://{cleaned}"
    try:
        parsed = urllib.parse.urlparse(candidate)
        port = parsed.port
    except ValueError as exc:
        return {"error": str(exc)}

    if not parsed.hostname:
        return {"error": "missing proxy host"}

    if port is None:
        port = _default_proxy_port(parsed.scheme)

    return {
        "source": source,
        "name": name,
        "value": cleaned,
        "url": candidate,
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": port,
    }


def _default_proxy_port(scheme: str) -> int | None:
    return {"http": 80, "https": 443, "socks": 1080, "socks5": 1080}.get(scheme)


def _parse_git_config_lines(stdout: str, scope: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        name, value = parts
        if "proxy" in name.casefold():
            entries[f"{scope}.{name}"] = value
    return entries


def _parse_windows_proxy_stdout(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    proxy_enabled = False
    proxy_server = ""
    for raw_line in stdout.replace("\x00", "").splitlines():
        line = raw_line.strip()
        if line.startswith("ProxyEnable="):
            proxy_enabled = line.split("=", 1)[1].strip() in {"1", "True", "true"}
        elif line.startswith("ProxyServer="):
            proxy_server = line.split("=", 1)[1].strip()
        elif line.startswith("AutoConfigURL="):
            auto_config = line.split("=", 1)[1].strip()
            if auto_config:
                values["system.autoconfig_url"] = auto_config

    if not proxy_enabled or not proxy_server:
        return values

    for index, item in enumerate(proxy_server.split(";")):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            name, value = item.split("=", 1)
            values[f"system.{name.strip()}"] = value.strip()
        else:
            values[f"system.proxy_server_{index}"] = item
    return values


def _parse_scutil_proxy_stdout(stdout: str) -> dict[str, str]:
    raw: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        raw[key.strip()] = value.strip()

    values: dict[str, str] = {}
    for prefix, scheme in (("HTTP", "http"), ("HTTPS", "https"), ("SOCKS", "socks5")):
        enabled = raw.get(f"{prefix}Enable") == "1"
        host = raw.get(f"{prefix}Proxy")
        port = raw.get(f"{prefix}Port")
        if enabled and host and port:
            values[f"system.{scheme}"] = f"{scheme}://{host}:{port}"
    return values


def _gsettings(schema: str, key: str, timeout_seconds: float, details: dict[str, Any]) -> str:
    result = run_command(["gsettings", "get", schema, key], timeout=timeout_seconds)
    details[f"{schema}.{key}"] = _command_details(result)
    if result.returncode != 0:
        return ""
    return _clean_setting_value(result.stdout)


def _clean_setting_value(value: str) -> str:
    return value.strip().strip("'\"")


def _port_probe_result(port: int, timeout_seconds: float) -> dict[str, Any]:
    attempts = [_try_connect("127.0.0.1", port, timeout_seconds), _try_connect("::1", port, timeout_seconds)]
    listening_attempts = [attempt for attempt in attempts if attempt["listening"]]
    return {"listening": bool(listening_attempts), "attempts": attempts}


def _try_connect(host: str, port: int, timeout_seconds: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"host": host, "listening": True, "latency_ms": _elapsed_ms(started)}
    except OSError as exc:
        return {
            "host": host,
            "listening": False,
            "latency_ms": _elapsed_ms(started),
            "error": str(exc),
        }


def _select_proxy_endpoint(endpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable_endpoints = [
        endpoint for endpoint in endpoints if endpoint.get("diagnostic_scope") != "windows_host"
    ]
    if not usable_endpoints:
        return None
    priority = {"https_proxy": 0, "HTTPS_PROXY": 0, "http_proxy": 1, "HTTP_PROXY": 1}
    return sorted(usable_endpoints, key=lambda item: priority.get(str(item.get("name")), 10))[0]


def _find_process_matches(stdout: str) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("PID ") or line.startswith("Image Name"):
            continue
        candidates = _process_name_candidates(line)
        folded_candidates = [candidate.casefold() for candidate in candidates if candidate]
        for keyword in PROCESS_KEYWORDS:
            if any(keyword in candidate for candidate in folded_candidates):
                key = (keyword, line)
                if key not in seen:
                    seen.add(key)
                    matches.append({"keyword": keyword, "line": _truncate(line, 300)})
                break
    return matches[:20]


def _status_from_checks(
    checks: list[CheckResult],
    endpoints: list[dict[str, Any]],
) -> str:
    if _find_check(checks, "common_proxy_ports", success=False):
        return "abnormal"
    if endpoints and _find_check(checks, "github_via_proxy", success=False):
        return "abnormal"
    if any(
        not check["success"]
        for check in checks
        if check["name"] in {"proxy_environment_variables", "git_proxy_config", "system_proxy_config"}
    ):
        return "abnormal"
    return "normal"


def _summarize(checks: list[CheckResult], endpoints: list[dict[str, Any]]) -> str:
    port_check = _find_check(checks, "common_proxy_ports")
    github_check = _find_check(checks, "github_via_proxy")

    if port_check and not port_check["success"]:
        return "代理配置指向本机端口，但对应端口未监听，疑似残留代理配置或代理客户端未启动。"
    if endpoints and github_check and not github_check["success"]:
        return "已发现代理配置，但通过代理访问 GitHub 失败，需检查代理/VPN 客户端或出口节点。"
    if (
        endpoints
        and github_check
        and github_check["success"]
        and github_check["evidence"].startswith("未发现可用于测试")
    ):
        return "仅发现当前运行环境不可直接测试的系统代理配置，未发现 WSL 本机代理端口或进程异常。"
    if endpoints and github_check and github_check["success"]:
        return "代理配置存在，常见代理端口和 GitHub 代理访问检查未发现异常。"
    return "未发现会影响 GitHub 的代理/VPN 异常配置。"


def _find_check(
    checks: list[CheckResult],
    name: str,
    *,
    success: bool | None = None,
) -> CheckResult | None:
    for check in checks:
        if check["name"] != name:
            continue
        if success is not None and check["success"] != success:
            continue
        return check
    return None


def _is_local_host(host: str) -> bool:
    return host.casefold() in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _should_probe_as_local_port(endpoint: dict[str, Any]) -> bool:
    if not _is_local_host(str(endpoint.get("host", ""))):
        return False
    return endpoint.get("diagnostic_scope") != "windows_host"


def _process_name_candidates(line: str) -> list[str]:
    parts = line.split(maxsplit=2)
    if len(parts) >= 3 and parts[0].isdigit():
        command = parts[1]
        executable = os.path.basename(parts[2].split(maxsplit=1)[0])
        return [command, executable]
    return [os.path.basename(parts[0])]


def _command_details(result: CommandResult) -> dict[str, Any]:
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
    skill_schema=PROXY_VPN_DIAGNOSIS_SCHEMA,
    implement_function=diagnose_proxy_vpn,
)
