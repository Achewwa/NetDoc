from __future__ import annotations

from dataclasses import dataclass, field

from netdoc.core import NetDocAgent
from netdoc.skills import create_default_registry


@dataclass
class FakeLLM:
    responses: list[str]
    prompts: list[tuple[str, str]] = field(default_factory=list)

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        self.prompts.append((system, user))
        return self.responses.pop(0)


def test_agent_plans_executes_and_synthesizes(monkeypatch) -> None:
    module = __import__("netdoc.skills.service_connectivity", fromlist=[""])
    monkeypatch.setattr(
        module,
        "_resolve_host",
        lambda host, timeout: {
            "name": "dns_resolve",
            "success": True,
            "latency_ms": 1,
            "evidence": f"{host} resolved",
        },
    )
    monkeypatch.setattr(
        module,
        "_check_tcp_connect",
        lambda host, port, timeout: {
            "name": f"tcp_connect_{port}",
            "success": port == 443,
            "latency_ms": 1,
            "evidence": f"{host}:{port} checked",
        },
    )
    monkeypatch.setattr(
        module,
        "_check_https_tls",
        lambda host, port, timeout: {
            "name": f"https_tls_{port}",
            "success": True,
            "latency_ms": 1,
            "evidence": f"{host}:{port} HTTPS checked",
        },
    )

    llm = FakeLLM(
        responses=[
            (
                '{"skill":"service_connectivity","arguments":{"host":"github.com",'
                '"ports":[443,22],"protocols":["tcp","https"]},"reason":"check GitHub"}'
            ),
            "HTTPS 可达，但 SSH 端口不可达。",
        ]
    )
    agent = NetDocAgent(registry=create_default_registry(), llm=llm)

    result = agent.answer("GitHub 连不上是 DNS 问题、HTTPS 问题，还是 SSH 问题？")

    assert result.answer == "HTTPS 可达，但 SSH 端口不可达。"
    assert result.plan.skill_name == "service_connectivity"
    assert result.observation["status"] == "abnormal"
    assert len(llm.prompts) == 2


def test_agent_can_plan_dns_diagnosis(monkeypatch) -> None:
    module = __import__("netdoc.skills.dns_diagnosis", fromlist=[""])
    monkeypatch.setattr(
        module,
        "_dns_config_check",
        lambda timeout: {
            "name": "dns_config",
            "success": True,
            "latency_ms": None,
            "evidence": "DNS config readable",
        },
    )
    monkeypatch.setattr(
        module,
        "_resolve_domain",
        lambda domain, timeout: {
            "name": "dns_resolve",
            "success": True,
            "latency_ms": 2,
            "evidence": f"{domain} resolved",
            "details": {"addresses": ["93.184.216.34"]},
        },
    )
    monkeypatch.setattr(
        module,
        "_direct_public_ip_check",
        lambda domain, ips, port, timeout, use_tls: {
            "name": f"direct_public_ip_{port}",
            "success": True,
            "latency_ms": 3,
            "evidence": "direct IP works",
        },
    )

    llm = FakeLLM(
        responses=[
            '{"skill":"dns_diagnosis","arguments":{"domain":"example.com"},"reason":"check DNS"}',
            "DNS 正常。",
        ]
    )
    agent = NetDocAgent(registry=create_default_registry(), llm=llm)

    result = agent.answer("example.com 能解析吗？")

    assert result.answer == "DNS 正常。"
    assert result.plan.skill_name == "dns_diagnosis"
    assert result.observation["skill"] == "dns_diagnosis"
