"""Risk-gated network repair actions."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from netdoc.core.skill import Skill
from netdoc.utils.command import CommandResult, run_command
from netdoc.utils.json_types import CheckResult, Observation, RiskLevel, make_check, make_observation
from netdoc.utils.platform import is_linux, is_windows, is_wsl, platform_name


SKILL_NAME = "repair_actions"
RISK_ORDER: dict[RiskLevel, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
DEFAULT_PROXY_SERVICES = ("clash", "mihomo", "v2ray", "xray", "sing-box")


@dataclass(frozen=True)
class RepairActionDefinition:
    """Static metadata for one repair action."""

    name: str
    risk: RiskLevel
    description: str
    examples: tuple[str, ...]
    handler: Callable[[bool, dict[str, Any], float], list[CheckResult]]


REPAIR_ACTIONS_SCHEMA: dict[str, Any] = {
    "name": SKILL_NAME,
    "description": (
        "Plan or execute risk-gated network repair actions. The skill is dry-run by "
        "default. Risk levels: none is read-only inspection, low is cache refresh, "
        "medium changes proxy or proxy process state, high changes route, DNS server "
        "or network adapter state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "inspect_supported_actions",
                    "preflight_repair_environment",
                    "flush_dns_cache",
                    "clear_temp_cache",
                    "clear_git_proxy_config",
                    "disable_system_proxy",
                    "restart_proxy_process",
                    "set_dns_servers",
                    "restore_dns_auto",
                    "add_route",
                    "delete_route",
                    "restart_network_interface",
                ],
                "description": "Repair action name.",
            },
            "execute": {
                "type": "boolean",
                "description": "Whether to actually change local system state. Defaults to false.",
            },
            "allowed_risk": {
                "type": "string",
                "enum": ["none", "low", "medium", "high"],
                "description": "Maximum risk level allowed by the user. Defaults to none.",
            },
            "confirmation": {
                "type": "string",
                "description": (
                    "For medium/high execution, must equal 'EXECUTE <action>' exactly."
                ),
            },
            "options": {
                "type": "object",
                "description": "Action-specific options, such as interface, servers, route or service.",
                "additionalProperties": True,
            },
            "timeout_seconds": {
                "type": "number",
                "minimum": 0.2,
                "maximum": 60,
                "description": "Per-command timeout in seconds.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def repair_actions(
    action: str,
    execute: bool = False,
    allowed_risk: RiskLevel = "none",
    confirmation: str = "",
    options: dict[str, Any] | None = None,
    timeout_seconds: float = 5.0,
) -> Observation:
    """Plan or execute one risk-gated network repair action."""
    action_options = options or {}
    definition = ACTIONS[action]
    checks = [
        _selected_action_check(definition, execute, allowed_risk, action_options),
        _safety_gate_check(definition, execute, allowed_risk, confirmation),
    ]

    handler_ran = False
    executed = False
    blocked_reason = _blocked_reason(definition, execute, allowed_risk, confirmation)
    if blocked_reason:
        checks.append(
            make_check(
                "repair_execution",
                True,
                blocked_reason,
                details={"executed": False, "blocked": True},
            )
        )
    else:
        handler_ran = True
        executed = execute
        checks.extend(definition.handler(execute, action_options, timeout_seconds))

    status = _status_from_checks(checks)
    return make_observation(
        skill=SKILL_NAME,
        status=status,
        checks=checks,
        summary=_summarize(definition, execute, executed, blocked_reason, checks),
        risk_level=definition.risk,
        metadata={
            "platform": platform_name(),
            "action": action,
            "action_risk": definition.risk,
            "allowed_risk": allowed_risk,
            "execute_requested": execute,
            "executed": executed and blocked_reason == "",
            "handler_ran": handler_ran,
            "confirmation_required": _requires_confirmation(definition, allowed_risk),
            "confirmation_phrase": _confirmation_phrase(action)
            if _requires_confirmation(definition, allowed_risk)
            else "",
            "options": action_options,
        },
    )


def _inspect_supported_actions(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    del execute, options, timeout_seconds
    actions = [
        {
            "name": definition.name,
            "risk": definition.risk,
            "description": definition.description,
            "examples": list(definition.examples),
        }
        for definition in ACTIONS.values()
    ]
    return [
        make_check(
            "supported_repair_actions",
            True,
            f"当前支持 {len(actions)} 个修复动作，并按风险等级记录。",
            details={"actions": actions},
        )
    ]


def _preflight_repair_environment(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    del execute, options
    command_names = ["ip", "resolvectl", "systemctl", "gsettings", "powershell.exe"]
    availability = {name: shutil.which(name) for name in command_names}
    checks = [
        make_check(
            "repair_environment",
            True,
            f"当前平台为 {platform_name()}，已记录常用修复命令可用性。",
            details={
                "platform": platform_name(),
                "is_wsl": is_wsl(),
                "uid": os.geteuid() if hasattr(os, "geteuid") else None,
                "commands": availability,
                "timeout_seconds": timeout_seconds,
            },
        )
    ]
    if is_linux():
        checks.append(_command_check("default_route_snapshot", ["ip", "route"], timeout_seconds))
    return checks


def _flush_dns_cache(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    del options
    commands: list[list[str]] = []
    if is_windows():
        commands.append(["ipconfig", "/flushdns"])
    elif is_wsl():
        commands.extend(
            [
                ["resolvectl", "flush-caches"],
                ["powershell.exe", "-NoProfile", "-Command", "Clear-DnsClientCache"],
            ]
        )
    elif is_linux():
        commands.extend([["resolvectl", "flush-caches"], ["systemd-resolve", "--flush-caches"]])
    else:
        commands.append(["dscacheutil", "-flushcache"])

    if not execute:
        return [_planned_commands_check("flush_dns_cache_plan", commands)]
    return [_first_success_command_check("flush_dns_cache", commands, timeout_seconds)]


def _clear_temp_cache(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    del timeout_seconds
    requested_paths = options.get("paths")
    if isinstance(requested_paths, list) and requested_paths:
        paths = [str(path) for path in requested_paths if isinstance(path, str)]
    else:
        paths = [str(Path(tempfile.gettempdir()) / "netdoc")]

    unsafe_paths = [path for path in paths if not _is_safe_temp_path(Path(path))]
    if unsafe_paths:
        return [
            make_check(
                "clear_temp_cache",
                False,
                "拒绝清理非临时目录路径。",
                details={"requested_paths": paths, "unsafe_paths": unsafe_paths},
            )
        ]

    if not execute:
        return [
            make_check(
                "clear_temp_cache_plan",
                True,
                "将仅清理 NetDoc 临时缓存目录，不会触碰系统缓存目录。",
                details={"paths": paths},
            )
        ]

    removed: list[str] = []
    missing: list[str] = []
    errors: list[dict[str, str]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            missing.append(str(path))
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})

    return [
        make_check(
            "clear_temp_cache",
            not errors,
            f"已清理 {len(removed)} 个临时缓存路径，{len(missing)} 个路径不存在。",
            details={"removed": removed, "missing": missing, "errors": errors},
        )
    ]


def _disable_system_proxy(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    del options
    if is_windows() or is_wsl():
        command = [
            "powershell.exe" if is_wsl() else "powershell",
            "-NoProfile",
            "-Command",
            (
                "$path='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'; "
                "Set-ItemProperty -Path $path -Name ProxyEnable -Value 0; "
                "Remove-ItemProperty -Path $path -Name ProxyServer -ErrorAction SilentlyContinue; "
                "Remove-ItemProperty -Path $path -Name AutoConfigURL -ErrorAction SilentlyContinue"
            ),
        ]
        commands = [command]
    elif is_linux():
        commands = [["gsettings", "set", "org.gnome.system.proxy", "mode", "none"]]
    else:
        commands = [["networksetup", "-setwebproxystate", "Wi-Fi", "off"]]

    if not execute:
        return [_planned_commands_check("disable_system_proxy_plan", commands)]
    return [_all_commands_check("disable_system_proxy", commands, timeout_seconds)]


def _clear_git_proxy_config(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    scope = str(options.get("scope") or "local")
    if scope not in {"local", "global", "all"}:
        return [_invalid_options_check("clear_git_proxy_config", "options.scope 必须是 local/global/all。")]

    keys = options.get("keys")
    if isinstance(keys, list) and keys:
        proxy_keys = [str(key) for key in keys if isinstance(key, str) and key.endswith(".proxy")]
    else:
        proxy_keys = ["http.proxy", "https.proxy"]
    if not proxy_keys:
        return [_invalid_options_check("clear_git_proxy_config", "未提供可清理的 Git proxy key。")]

    cwd = str(options.get("cwd") or os.getcwd())
    commands = _git_proxy_unset_commands(scope, proxy_keys)
    if not execute:
        return [
            make_check(
                "clear_git_proxy_config_plan",
                True,
                "已生成清理 Git proxy 配置的命令；dry-run 模式未修改仓库或全局 Git 配置。",
                details={"commands": commands, "cwd": cwd, "executed": False},
            )
        ]

    results = [
        _command_details(run_command(command, timeout=timeout_seconds, cwd=cwd))
        for command in commands
    ]
    success = all(result["returncode"] in {0, 5} for result in results)
    return [
        make_check(
            "clear_git_proxy_config",
            success,
            "Git proxy 配置已清理或原本不存在。"
            if success
            else "至少一个 Git proxy 配置清理命令执行失败。",
            details={"commands": results, "cwd": cwd, "executed": True},
        )
    ]


def _restart_proxy_process(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    service = options.get("service")
    if not isinstance(service, str) or not service:
        service = ""
    if service and service not in DEFAULT_PROXY_SERVICES:
        return [
            make_check(
                "restart_proxy_process",
                False,
                "拒绝重启不在默认代理服务白名单内的服务。",
                details={"service": service, "allowed_services": list(DEFAULT_PROXY_SERVICES)},
            )
        ]

    service_names = [service] if service else list(DEFAULT_PROXY_SERVICES)
    commands = [["systemctl", "--user", "restart", item] for item in service_names]
    if not is_linux():
        commands = [
            [
                "powershell.exe" if is_wsl() else "powershell",
                "-NoProfile",
                "-Command",
                f"Get-Process -Name '{item}' -ErrorAction SilentlyContinue | Restart-Process",
            ]
            for item in service_names
        ]

    if not execute:
        return [_planned_commands_check("restart_proxy_process_plan", commands)]
    return [_first_success_command_check("restart_proxy_process", commands, timeout_seconds)]


def _set_dns_servers(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    interface = options.get("interface")
    servers = options.get("servers")
    if not isinstance(interface, str) or not interface:
        return [_invalid_options_check("set_dns_servers", "需要 options.interface。")]
    if not isinstance(servers, list) or not servers or not all(isinstance(item, str) for item in servers):
        return [_invalid_options_check("set_dns_servers", "需要 options.servers 字符串数组。")]

    if is_windows() or is_wsl():
        server_list = ",".join(f"'{server}'" for server in servers)
        command = [
            "powershell.exe" if is_wsl() else "powershell",
            "-NoProfile",
            "-Command",
            (
                f"Set-DnsClientServerAddress -InterfaceAlias '{interface}' "
                f"-ServerAddresses ({server_list})"
            ),
        ]
    else:
        command = ["resolvectl", "dns", interface, *servers]
    commands = [command]

    if not execute:
        return [_planned_commands_check("set_dns_servers_plan", commands)]
    return [_all_commands_check("set_dns_servers", commands, timeout_seconds)]


def _restore_dns_auto(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    interface = options.get("interface")
    if not isinstance(interface, str) or not interface:
        return [_invalid_options_check("restore_dns_auto", "需要 options.interface。")]

    if is_windows() or is_wsl():
        command = [
            "powershell.exe" if is_wsl() else "powershell",
            "-NoProfile",
            "-Command",
            f"Set-DnsClientServerAddress -InterfaceAlias '{interface}' -ResetServerAddresses",
        ]
    else:
        command = ["resolvectl", "revert", interface]
    commands = [command]

    if not execute:
        return [_planned_commands_check("restore_dns_auto_plan", commands)]
    return [_all_commands_check("restore_dns_auto", commands, timeout_seconds)]


def _add_route(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    route = options.get("route")
    gateway = options.get("gateway")
    interface = options.get("interface")
    if not isinstance(route, str) or not route:
        return [_invalid_options_check("add_route", "需要 options.route。")]
    if not isinstance(gateway, str) or not gateway:
        return [_invalid_options_check("add_route", "需要 options.gateway。")]

    command = ["ip", "route", "add", route, "via", gateway]
    if isinstance(interface, str) and interface:
        command.extend(["dev", interface])
    commands = [command]

    if not execute:
        return [_planned_commands_check("add_route_plan", commands)]
    return [_all_commands_check("add_route", commands, timeout_seconds)]


def _delete_route(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    route = options.get("route")
    if not isinstance(route, str) or not route:
        return [_invalid_options_check("delete_route", "需要 options.route。")]

    command = ["ip", "route", "delete", route]
    gateway = options.get("gateway")
    interface = options.get("interface")
    if isinstance(gateway, str) and gateway:
        command.extend(["via", gateway])
    if isinstance(interface, str) and interface:
        command.extend(["dev", interface])
    commands = [command]

    if not execute:
        return [_planned_commands_check("delete_route_plan", commands)]
    return [_all_commands_check("delete_route", commands, timeout_seconds)]


def _restart_network_interface(
    execute: bool,
    options: dict[str, Any],
    timeout_seconds: float,
) -> list[CheckResult]:
    interface = options.get("interface")
    if not isinstance(interface, str) or not interface:
        return [_invalid_options_check("restart_network_interface", "需要 options.interface。")]

    if is_windows() or is_wsl():
        command = [
            "powershell.exe" if is_wsl() else "powershell",
            "-NoProfile",
            "-Command",
            f"Restart-NetAdapter -Name '{interface}' -Confirm:$false",
        ]
        commands = [command]
    else:
        commands = [["ip", "link", "set", interface, "down"], ["ip", "link", "set", interface, "up"]]

    if not execute:
        return [_planned_commands_check("restart_network_interface_plan", commands)]
    return [_all_commands_check("restart_network_interface", commands, timeout_seconds)]


def _selected_action_check(
    definition: RepairActionDefinition,
    execute: bool,
    allowed_risk: RiskLevel,
    options: dict[str, Any],
) -> CheckResult:
    return make_check(
        "repair_action_selected",
        True,
        f"已选择 {definition.name}，风险等级为 {definition.risk}。",
        details={
            "action": definition.name,
            "risk": definition.risk,
            "execute_requested": execute,
            "allowed_risk": allowed_risk,
            "options": options,
        },
    )


def _safety_gate_check(
    definition: RepairActionDefinition,
    execute: bool,
    allowed_risk: RiskLevel,
    confirmation: str,
) -> CheckResult:
    blocked_reason = _blocked_reason(definition, execute, allowed_risk, confirmation)
    if blocked_reason:
        return make_check(
            "repair_safety_gate",
            True,
            blocked_reason,
            details={
                "blocked": True,
                "required_allowed_risk": definition.risk,
                "allowed_risk": allowed_risk,
                "confirmation_required": _requires_confirmation(definition, allowed_risk),
                "confirmation_phrase": _confirmation_phrase(definition.name)
                if _requires_confirmation(definition, allowed_risk)
                else "",
            },
        )
    return make_check(
        "repair_safety_gate",
        True,
        "安全门禁已通过，允许执行该修复动作。" if execute else "dry-run 模式仅生成计划。",
        details={"blocked": False, "dry_run": not execute},
    )


def _blocked_reason(
    definition: RepairActionDefinition,
    execute: bool,
    allowed_risk: RiskLevel,
    confirmation: str,
) -> str:
    if not execute:
        return ""
    if not _requires_confirmation(definition, allowed_risk):
        return ""
    if confirmation != _confirmation_phrase(definition.name):
        return (
            f"已阻止执行：动作风险 {definition.risk} 高于当前自动执行阈值 "
            f"{allowed_risk}，需要用户确认。"
        )
    return ""


def _requires_confirmation(
    definition: RepairActionDefinition,
    allowed_risk: RiskLevel,
) -> bool:
    return RISK_ORDER[definition.risk] > RISK_ORDER[allowed_risk]


def _confirmation_phrase(action: str) -> str:
    return f"EXECUTE {action}"


def _planned_commands_check(name: str, commands: list[list[str]]) -> CheckResult:
    return make_check(
        name,
        True,
        "已生成待执行命令；dry-run 模式未修改系统。",
        details={"commands": commands, "executed": False},
    )


def _git_proxy_unset_commands(scope: str, keys: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    scopes = ["local", "global"] if scope == "all" else [scope]
    for item in scopes:
        scope_flag = "--local" if item == "local" else "--global"
        for key in keys:
            commands.append(["git", "config", scope_flag, "--unset", key])
    return commands


def _all_commands_check(name: str, commands: list[list[str]], timeout_seconds: float) -> CheckResult:
    results = [_run_repair_command(command, timeout_seconds) for command in commands]
    success = all(result["returncode"] == 0 for result in results)
    return make_check(
        name,
        success,
        "修复命令全部执行成功。" if success else "至少一个修复命令执行失败。",
        details={"commands": results, "executed": True},
    )


def _first_success_command_check(
    name: str,
    commands: list[list[str]],
    timeout_seconds: float,
) -> CheckResult:
    results: list[dict[str, Any]] = []
    for command in commands:
        result = _run_repair_command(command, timeout_seconds)
        results.append(result)
        if result["returncode"] == 0:
            return make_check(
                name,
                True,
                "至少一个候选修复命令执行成功。",
                details={"commands": results, "executed": True},
            )
    return make_check(
        name,
        False,
        "所有候选修复命令均执行失败或不可用。",
        details={"commands": results, "executed": True},
    )


def _command_check(name: str, command: list[str], timeout_seconds: float) -> CheckResult:
    result = run_command(command, timeout=timeout_seconds)
    return make_check(
        name,
        result.returncode == 0,
        "命令执行成功。" if result.returncode == 0 else "命令执行失败。",
        details=_command_details(result),
    )


def _run_repair_command(command: list[str], timeout_seconds: float) -> dict[str, Any]:
    return _command_details(run_command(command, timeout=timeout_seconds))


def _command_details(result: CommandResult) -> dict[str, Any]:
    return {
        "command": result.command,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "timed_out": result.timed_out,
    }


def _invalid_options_check(name: str, evidence: str) -> CheckResult:
    return make_check(name, False, evidence, details={"invalid_options": True})


def _is_safe_temp_path(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    temp_root = Path(tempfile.gettempdir()).resolve()
    return resolved == temp_root / "netdoc" or (
        temp_root in resolved.parents and "netdoc" in resolved.parts
    )


def _status_from_checks(checks: list[CheckResult]) -> str:
    return "normal" if all(check["success"] for check in checks) else "abnormal"


def _summarize(
    definition: RepairActionDefinition,
    execute: bool,
    executed: bool,
    blocked_reason: str,
    checks: list[CheckResult],
) -> str:
    if blocked_reason:
        return f"{definition.name} 未执行：{blocked_reason}"
    if definition.risk == "none":
        return f"{definition.name} 已完成只读检查。"
    if not execute:
        return f"{definition.name} 已生成 dry-run 修复计划，未修改系统。"
    if executed and all(check["success"] for check in checks):
        return f"{definition.name} 已执行完成。"
    return f"{definition.name} 执行后仍有失败项，请查看 checks 证据。"


ACTIONS: dict[str, RepairActionDefinition] = {
    "inspect_supported_actions": RepairActionDefinition(
        name="inspect_supported_actions",
        risk="none",
        description="列出所有支持的修复动作、风险等级和示例。",
        examples=("查看 repair_actions 能做什么", "列出可用修复动作"),
        handler=_inspect_supported_actions,
    ),
    "preflight_repair_environment": RepairActionDefinition(
        name="preflight_repair_environment",
        risk="none",
        description="只读检查当前平台和常用修复命令可用性。",
        examples=("修复前先检查当前环境", "只读预检网络修复能力"),
        handler=_preflight_repair_environment,
    ),
    "flush_dns_cache": RepairActionDefinition(
        name="flush_dns_cache",
        risk="low",
        description="刷新本机或宿主系统 DNS 缓存。",
        examples=("刷新 DNS 缓存", "DNS 解析失败后先清缓存"),
        handler=_flush_dns_cache,
    ),
    "clear_temp_cache": RepairActionDefinition(
        name="clear_temp_cache",
        risk="low",
        description="清理 NetDoc 自己的临时缓存目录。",
        examples=("清理 NetDoc 临时缓存",),
        handler=_clear_temp_cache,
    ),
    "clear_git_proxy_config": RepairActionDefinition(
        name="clear_git_proxy_config",
        risk="medium",
        description="清理当前仓库或全局 Git proxy 配置，适合残留 Git proxy 指向失效端口的场景。",
        examples=("清理当前仓库 Git proxy", "移除残留 Git proxy 配置"),
        handler=_clear_git_proxy_config,
    ),
    "disable_system_proxy": RepairActionDefinition(
        name="disable_system_proxy",
        risk="medium",
        description="关闭系统代理设置，适合残留代理导致直连失败的场景。",
        examples=("关闭系统代理", "清掉残留 Windows 系统代理"),
        handler=_disable_system_proxy,
    ),
    "restart_proxy_process": RepairActionDefinition(
        name="restart_proxy_process",
        risk="medium",
        description="通过服务管理器重启常见代理客户端服务。",
        examples=("重启 mihomo 代理服务", "重启 Clash 服务"),
        handler=_restart_proxy_process,
    ),
    "set_dns_servers": RepairActionDefinition(
        name="set_dns_servers",
        risk="high",
        description="为指定网卡设置 DNS 服务器。",
        examples=("把 Wi-Fi DNS 改成 223.5.5.5",),
        handler=_set_dns_servers,
    ),
    "restore_dns_auto": RepairActionDefinition(
        name="restore_dns_auto",
        risk="high",
        description="恢复指定网卡自动 DNS。",
        examples=("恢复 Wi-Fi 自动 DNS",),
        handler=_restore_dns_auto,
    ),
    "add_route": RepairActionDefinition(
        name="add_route",
        risk="high",
        description="添加路由表项。",
        examples=("添加到内网网段的静态路由",),
        handler=_add_route,
    ),
    "delete_route": RepairActionDefinition(
        name="delete_route",
        risk="high",
        description="删除路由表项。",
        examples=("删除残留 VPN 路由",),
        handler=_delete_route,
    ),
    "restart_network_interface": RepairActionDefinition(
        name="restart_network_interface",
        risk="high",
        description="重启指定网络接口或网卡。",
        examples=("重启 Wi-Fi 网卡",),
        handler=_restart_network_interface,
    ),
}


skill = Skill(
    name=SKILL_NAME,
    skill_schema=REPAIR_ACTIONS_SCHEMA,
    implement_function=repair_actions,
)
