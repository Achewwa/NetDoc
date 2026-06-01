from __future__ import annotations

import sys

import pytest

from netdoc.core import SchemaValidationError, Skill, SkillRegistry
from netdoc.skills import service_connectivity
from netdoc.skills import dns_diagnosis
from netdoc.skills import proxy_vpn_diagnosis
from netdoc.skills import repair_actions
from netdoc.skills import report_generator
from netdoc.skills import service_connectivity as service_skill
from netdoc.skills import service_connectivity as service_skill_alias
from netdoc.skills.service_connectivity import skill
from netdoc.core.llm import _content_text
from netdoc.utils.command import run_command
from netdoc.utils.json_types import make_check


def test_skill_validates_required_and_types() -> None:
    local_skill = Skill(
        name="demo",
        skill_schema={
            "parameters": {
                "type": "object",
                "properties": {"host": {"type": "string"}},
                "required": ["host"],
                "additionalProperties": False,
            }
        },
        implement_function=lambda host: {"host": host},
    )

    assert local_skill.run({"host": "github.com"}) == {"host": "github.com"}
    with pytest.raises(SchemaValidationError):
        local_skill.run({})
    with pytest.raises(SchemaValidationError):
        local_skill.run({"host": "github.com", "extra": True})


def test_registry_registers_and_finds_skills() -> None:
    registry = SkillRegistry()
    registry.register(skill)

    assert registry.has("service_connectivity")
    assert registry.get("service_connectivity") is skill
    assert registry.find("HTTPS") == [skill]
    assert registry.names() == ["service_connectivity"]

    with pytest.raises(ValueError):
        registry.register(skill)


def test_run_command_captures_output() -> None:
    result = run_command([sys.executable, "-c", "print('ok')"], timeout=2)

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert not result.timed_out


def test_run_command_reports_missing_executable() -> None:
    result = run_command(["netdoc-command-that-does-not-exist"], timeout=2)

    assert result.returncode == 127
    assert result.stdout == ""
    assert "No such file or directory" in result.stderr
    assert not result.timed_out


def test_llm_content_text_prefers_text_blocks() -> None:
    response = {
        "content": [
            {"type": "thinking", "thinking": "internal reasoning"},
            {"type": "text", "text": '{"candidate_skills":[]}'},
        ]
    }

    assert _content_text(response) == '{"candidate_skills":[]}'


def test_llm_content_text_falls_back_to_thinking_blocks() -> None:
    response = {
        "content": [
            {
                "type": "thinking",
                "thinking": 'reasoning...\n{"candidate_skills":[{"skill":"proxy_vpn_diagnosis","arguments":{}}]}',
            }
        ]
    }

    assert '"proxy_vpn_diagnosis"' in _content_text(response)


def test_service_connectivity_summarizes_https_up_ssh_down(monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("netdoc.skills.service_connectivity", fromlist=[""])

    monkeypatch.setattr(
        module,
        "_resolve_host",
        lambda host, timeout: make_check("dns_resolve", True, f"{host} resolved"),
    )
    monkeypatch.setattr(
        module,
        "_check_tcp_connect",
        lambda host, port, timeout: make_check(
            f"tcp_connect_{port}", port == 443, f"{host}:{port} checked"
        ),
    )
    monkeypatch.setattr(
        module,
        "_check_https_tls",
        lambda host, port, timeout: make_check(
            f"https_tls_{port}", True, f"{host}:{port} HTTPS checked"
        ),
    )

    observation = skill.run(
        {"host": "github.com", "ports": [443, 22], "protocols": ["tcp", "https"]}
    )

    assert observation["skill"] == "service_connectivity"
    assert observation["status"] == "abnormal"
    assert observation["summary"] == "HTTPS 可达，但 SSH 端口不可达。"
    assert [check["name"] for check in observation["checks"]] == [
        "dns_resolve",
        "tcp_connect_443",
        "tcp_connect_22",
        "https_tls_443",
    ]


def test_service_connectivity_rejects_invalid_port() -> None:
    with pytest.raises(SchemaValidationError):
        skill.run({"host": "github.com", "ports": [70000], "protocols": ["tcp"]})


def test_skills_init_exports_service_connectivity() -> None:
    assert service_connectivity is service_skill
    assert service_skill is service_skill_alias
    assert dns_diagnosis.name == "dns_diagnosis"
    assert proxy_vpn_diagnosis.name == "proxy_vpn_diagnosis"
    assert repair_actions.name == "repair_actions"
    assert report_generator.name == "report_generator"
