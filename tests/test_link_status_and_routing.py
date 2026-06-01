from __future__ import annotations

from netdoc.skills import create_default_registry, link_status, routing_diagnosis
from netdoc.utils.command import CommandResult


def _command(returncode: int = 0) -> CommandResult:
    return CommandResult(["ip"], returncode, "[]", "")


def test_link_status_reports_normal_link(monkeypatch) -> None:
    module = __import__("netdoc.skills.link_status", fromlist=[""])
    monkeypatch.setattr(
        module,
        "_collect_link_snapshot",
        lambda timeout: {
            "interfaces": [
                {
                    "name": "eth0",
                    "operstate": "UP",
                    "flags": ["BROADCAST", "UP", "LOWER_UP"],
                    "is_loopback": False,
                    "is_enabled": True,
                    "addresses": [
                        {
                            "family": "inet",
                            "address": "192.168.1.20",
                            "prefixlen": 24,
                            "scope": "global",
                            "is_usable": True,
                        }
                    ],
                }
            ],
            "default_routes": [{"gateway": "192.168.1.1", "dev": "eth0", "metric": 100}],
            "addr_command": _command(),
            "route_command": _command(),
        },
    )
    monkeypatch.setattr(
        module,
        "_ping_gateway",
        lambda gateway, timeout: CommandResult(["ping"], 0, "ok", ""),
    )

    observation = link_status.run({})

    assert observation["skill"] == "link_status"
    assert observation["status"] == "normal"
    assert [check["name"] for check in observation["checks"]] == [
        "adapter_enabled",
        "ip_address_valid",
        "default_gateway_exists",
        "gateway_reachable",
    ]


def test_link_status_flags_missing_gateway(monkeypatch) -> None:
    module = __import__("netdoc.skills.link_status", fromlist=[""])
    monkeypatch.setattr(
        module,
        "_collect_link_snapshot",
        lambda timeout: {
            "interfaces": [
                {
                    "name": "eth0",
                    "operstate": "UP",
                    "flags": ["UP"],
                    "is_loopback": False,
                    "is_enabled": True,
                    "addresses": [
                        {
                            "family": "inet",
                            "address": "192.168.1.20",
                            "prefixlen": 24,
                            "scope": "global",
                            "is_usable": True,
                        }
                    ],
                }
            ],
            "default_routes": [],
            "addr_command": _command(),
            "route_command": _command(),
        },
    )

    observation = link_status.run({})

    assert observation["status"] == "abnormal"
    assert observation["summary"] == "本机缺少默认网关，无法正常访问跨网段或公网目标。"
    assert not observation["checks"][2]["success"]
    assert not observation["checks"][3]["success"]


def test_routing_diagnosis_flags_multiple_interfaces_and_metric_conflict(monkeypatch) -> None:
    module = __import__("netdoc.skills.routing_diagnosis", fromlist=[""])
    interfaces = [
        {
            "name": "eth0",
            "operstate": "UP",
            "flags": ["UP"],
            "is_loopback": False,
            "is_enabled": True,
            "is_vpn_virtual": False,
            "addresses": [{"address": "192.168.1.20", "is_usable": True}],
        },
        {
            "name": "wlan0",
            "operstate": "UP",
            "flags": ["UP"],
            "is_loopback": False,
            "is_enabled": True,
            "is_vpn_virtual": False,
            "addresses": [{"address": "10.0.0.20", "is_usable": True}],
        },
    ]
    monkeypatch.setattr(module, "is_wsl", lambda: False)
    monkeypatch.setattr(
        module,
        "_collect_routing_snapshot",
        lambda timeout: {
            "interfaces": interfaces,
            "routes": [
                {"dst": "default", "gateway": "192.168.1.1", "dev": "eth0", "metric": 100},
                {"dst": "default", "gateway": "10.0.0.1", "dev": "wlan0", "metric": 100},
            ],
            "rules": [],
            "route_command": _command(),
        },
    )

    observation = routing_diagnosis.run({})
    failed = [check["name"] for check in observation["checks"] if not check["success"]]

    assert observation["status"] == "abnormal"
    assert failed == ["multiple_active_interfaces", "route_priority"]
    assert observation["summary"] == (
        "默认路由存在，但路由优先级可能冲突，需检查 metric、虚拟网卡或残留 VPN 路由。"
    )


def test_routing_diagnosis_checks_wsl_gateway(monkeypatch) -> None:
    module = __import__("netdoc.skills.routing_diagnosis", fromlist=[""])
    monkeypatch.setattr(module, "is_wsl", lambda: True)
    monkeypatch.setattr(module, "_read_resolv_nameservers", lambda: ["172.20.0.1"])
    monkeypatch.setattr(
        module,
        "_ping_gateway",
        lambda gateway, timeout: CommandResult(["ping"], 0, "ok", ""),
    )
    monkeypatch.setattr(
        module,
        "_collect_routing_snapshot",
        lambda timeout: {
            "interfaces": [
                {
                    "name": "eth0",
                    "operstate": "UP",
                    "flags": ["UP"],
                    "is_loopback": False,
                    "is_enabled": True,
                    "is_vpn_virtual": False,
                    "addresses": [{"address": "172.20.0.10", "is_usable": True}],
                }
            ],
            "routes": [{"dst": "default", "gateway": "172.20.0.1", "dev": "eth0"}],
            "rules": [{"priority": "0", "rule": "from all lookup local"}],
            "route_command": _command(),
        },
    )

    observation = routing_diagnosis.run({})

    assert observation["status"] == "normal"
    assert observation["checks"][3]["name"] == "wsl_gateway"
    assert observation["checks"][3]["success"]
    assert observation["metadata"]["route_rules"] == [
        {"priority": "0", "rule": "from all lookup local"}
    ]


def test_default_registry_exports_new_low_level_skills() -> None:
    registry = create_default_registry()

    assert registry.has("link_status")
    assert registry.has("routing_diagnosis")
