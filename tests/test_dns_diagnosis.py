from __future__ import annotations

import socket

import pytest

from netdoc.core import SchemaValidationError
from netdoc.skills.dns_diagnosis import skill
from netdoc.utils.json_types import make_check


class FakeSocket:
    def __enter__(self) -> "FakeSocket":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeTLSContext:
    def wrap_socket(self, raw_socket, *, server_hostname: str):
        return raw_socket


def test_dns_diagnosis_reports_resolution_and_direct_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("netdoc.skills.dns_diagnosis", fromlist=[""])

    monkeypatch.setattr(module, "is_linux", lambda: False)
    monkeypatch.setattr(module, "_read_resolv_conf", lambda: "nameserver 8.8.8.8\n")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda domain, port, type: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))
        ],
    )
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: FakeSocket())
    monkeypatch.setattr(module.ssl, "create_default_context", lambda: FakeTLSContext())

    observation = skill.run({"domain": "example.com", "port": 443})

    assert observation["skill"] == "dns_diagnosis"
    assert observation["status"] == "normal"
    assert observation["metadata"]["resolved_ips"] == ["93.184.216.34"]
    assert observation["metadata"]["public_ips"] == ["93.184.216.34"]
    assert [check["name"] for check in observation["checks"]] == [
        "dns_config",
        "dns_resolve",
        "public_ip_classification",
        "direct_public_ip_443",
    ]


def test_dns_diagnosis_uses_nslookup_when_socket_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.dns_diagnosis", fromlist=[""])

    monkeypatch.setattr(module, "is_linux", lambda: False)
    monkeypatch.setattr(module, "_read_resolv_conf", lambda: "nameserver 1.1.1.1\n")
    monkeypatch.setattr(
        module,
        "_resolve_domain",
        lambda domain, timeout: make_check("dns_resolve", False, f"{domain} failed"),
    )
    monkeypatch.setattr(
        module,
        "_nslookup_check",
        lambda domain, timeout: make_check("nslookup", False, "nslookup failed"),
    )

    observation = skill.run({"domain": "bad.example"})

    assert observation["status"] == "abnormal"
    assert observation["summary"] == "域名解析失败，已记录当前 DNS 配置和 nslookup 辅助结果。"
    assert [check["name"] for check in observation["checks"]] == [
        "dns_config",
        "dns_resolve",
        "nslookup",
    ]


def test_dns_diagnosis_rejects_invalid_port() -> None:
    with pytest.raises(SchemaValidationError):
        skill.run({"domain": "example.com", "port": 70000})
