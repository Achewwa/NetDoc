from __future__ import annotations

from dataclasses import dataclass, field

from netdoc.core import NetDocAgent
from netdoc.core.planner import _extract_json
from netdoc.skills import create_default_registry
from netdoc.utils.command import CommandResult
from scripts.ask_agent import (
    ConversationState,
    _ask_once,
    _build_continue_question,
    _handle_continue_command,
    _handle_issues_command,
    _handle_repair_command,
    _handle_risk_command,
    _maybe_prompt_repair_confirmation,
    _repair_confirmation_prompt,
    _repair_planned_commands,
)


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


def test_planner_extract_json_skips_reasoning_prefix() -> None:
    data = _extract_json(
        'reasoning with {"not":"the final object"\n'
        'final answer:\n{"candidate_skills":[{"skill":"proxy_vpn_diagnosis","arguments":{}}]}'
    )

    assert data["candidate_skills"][0]["skill"] == "proxy_vpn_diagnosis"


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
    assert result.report_observation is not None
    assert result.report_observation["skill"] == "report_generator"
    assert result.report_observation["metadata"]["called_skills"] == ["service_connectivity"]
    assert "HTTPS 可达，但 SSH 端口不可达。" in result.report_observation["metadata"][
        "report_markdown"
    ]
    assert result.to_dict()["report_observation"]["skill"] == "report_generator"
    assert result.to_dict()["plan"] == {
        "mode": "multi_step",
        "candidate_skills": [
            {
                "skill": "service_connectivity",
                "arguments": {
                    "host": "github.com",
                    "ports": [443, 22],
                    "protocols": ["tcp", "https"],
                },
                "reason": "check GitHub",
            }
        ],
    }
    assert result.to_dict()["executed_steps"] == [
        {
            "phase": "diagnosis",
            "step": 1,
            "skill": "service_connectivity",
            "arguments": {"host": "github.com", "ports": [443, 22], "protocols": ["tcp", "https"]},
            "reason": "check GitHub",
            "observation_key": "observations[0]",
        },
        {
            "phase": "report",
            "step": 2,
            "skill": "report_generator",
            "arguments": {"observations": "observations"},
            "observation_key": "report_observation",
        },
    ]
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
    assert result.report_observation is not None
    assert result.report_observation["skill"] == "report_generator"


def test_agent_applies_runtime_allowed_risk_to_repair_actions(monkeypatch) -> None:
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
            "success": False,
            "latency_ms": 2,
            "evidence": f"{domain} failed",
        },
    )
    monkeypatch.setattr(
        module,
        "_nslookup_check",
        lambda domain, timeout: {
            "name": "nslookup",
            "success": False,
            "latency_ms": 2,
            "evidence": "nslookup failed",
        },
    )
    llm = FakeLLM(
        responses=[
            '{"skill":"dns_diagnosis","arguments":{"domain":"example.com"},"reason":"check DNS"}',
            "已生成 DNS 缓存刷新计划，未执行系统变更。",
        ]
    )
    agent = NetDocAgent(registry=create_default_registry(), llm=llm)

    result = agent.answer("刷新 DNS 缓存", allowed_risk="low")

    assert result.plan.skill_name == "dns_diagnosis"
    assert result.repair_call is not None
    assert result.repair_call.arguments["allowed_risk"] == "low"
    assert result.repair_observation is not None
    assert result.repair_observation["metadata"]["allowed_risk"] == "low"
    assert not result.repair_observation["metadata"]["executed"]
    assert result.unresolved_issues[0]["skill"] == "dns_diagnosis"
    assert result.unresolved_issues[0]["repair_plan"]["arguments"]["action"] == "flush_dns_cache"


def test_agent_collects_multiple_abnormal_observations_before_repair(monkeypatch) -> None:
    dns_module = __import__("netdoc.skills.dns_diagnosis", fromlist=[""])
    service_module = __import__("netdoc.skills.service_connectivity", fromlist=[""])
    monkeypatch.setattr(
        dns_module,
        "_dns_config_check",
        lambda timeout: {
            "name": "dns_config",
            "success": True,
            "latency_ms": None,
            "evidence": "DNS config readable",
        },
    )
    monkeypatch.setattr(
        dns_module,
        "_resolve_domain",
        lambda domain, timeout: {
            "name": "dns_resolve",
            "success": False,
            "latency_ms": 2,
            "evidence": f"{domain} failed",
        },
    )
    monkeypatch.setattr(
        dns_module,
        "_nslookup_check",
        lambda domain, timeout: {
            "name": "nslookup",
            "success": False,
            "latency_ms": 2,
            "evidence": "nslookup failed",
        },
    )
    monkeypatch.setattr(
        service_module,
        "_resolve_host",
        lambda host, timeout: {
            "name": "dns_resolve",
            "success": True,
            "latency_ms": 1,
            "evidence": f"{host} resolved",
        },
    )
    monkeypatch.setattr(
        service_module,
        "_check_tcp_connect",
        lambda host, port, timeout: {
            "name": f"tcp_connect_{port}",
            "success": False,
            "latency_ms": 1,
            "evidence": f"{host}:{port} failed",
        },
    )
    monkeypatch.setattr(
        service_module,
        "_check_https_tls",
        lambda host, port, timeout: {
            "name": f"https_tls_{port}",
            "success": False,
            "latency_ms": 1,
            "evidence": f"{host}:{port} HTTPS failed",
        },
    )

    llm = FakeLLM(
        responses=[
            (
                '{"candidate_skills":['
                '{"skill":"dns_diagnosis","arguments":{"domain":"example.com"},'
                '"reason":"check DNS"},'
                '{"skill":"service_connectivity","arguments":{"host":"github.com",'
                '"ports":[443],"protocols":["tcp","https"]},"reason":"check HTTPS"}'
                '],"reason":"check DNS and service"}'
            ),
            "发现 DNS 和服务连通性异常，已生成低风险 DNS 修复计划，HTTPS 异常仍需继续排查。",
        ]
    )
    agent = NetDocAgent(registry=create_default_registry(), llm=llm)

    result = agent.answer("检查 DNS 和 GitHub HTTPS")

    assert [observation["skill"] for observation in result.observations] == [
        "dns_diagnosis",
        "service_connectivity",
    ]
    assert result.repair_call is not None
    assert result.repair_call.arguments["action"] == "flush_dns_cache"
    assert result.unresolved_issues[0]["skill"] == "service_connectivity"
    assert [step["phase"] for step in result.executed_steps] == ["diagnosis", "diagnosis", "repair", "report"]


def test_agent_maps_stale_git_proxy_to_clear_git_proxy_config(monkeypatch) -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setattr(
        module,
        "_git_proxy_check",
        lambda timeout: (
            {
                "name": "git_proxy_config",
                "success": True,
                "latency_ms": None,
                "evidence": "发现 Git proxy 配置: local.http.proxy=http://127.0.0.1:7897",
                "details": {
                    "entries": {"local.http.proxy": "http://127.0.0.1:7897"},
                    "endpoints": [
                        {
                            "source": "git",
                            "name": "local.http.proxy",
                            "url": "http://127.0.0.1:7897",
                            "host": "127.0.0.1",
                            "port": 7897,
                        }
                    ],
                },
            },
            [
                {
                    "source": "git",
                    "name": "local.http.proxy",
                    "url": "http://127.0.0.1:7897",
                    "host": "127.0.0.1",
                    "port": 7897,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        module,
        "_system_proxy_check",
        lambda timeout: (
            {
                "name": "system_proxy_config",
                "success": True,
                "latency_ms": None,
                "evidence": "未发现已启用的系统代理配置。",
            },
            [],
        ),
    )
    monkeypatch.setattr(
        module,
        "_port_probe_result",
        lambda port, timeout: {"listening": False, "attempts": []},
    )
    monkeypatch.setattr(
        module,
        "_github_via_proxy_check",
        lambda target_url, endpoints, timeout: {
            "name": "github_via_proxy",
            "success": False,
            "latency_ms": None,
            "evidence": "通过代理 http://127.0.0.1:7897 访问 https://github.com 失败。",
        },
    )
    monkeypatch.setattr(
        module,
        "_proxy_vpn_process_check",
        lambda timeout: {
            "name": "clash_vpn_processes",
            "success": True,
            "latency_ms": None,
            "evidence": "未发现 Clash/VPN 相关进程。",
        },
    )

    llm = FakeLLM(
        responses=[
            (
                '{"skill":"proxy_vpn_diagnosis","arguments":{"target_url":"https://github.com",'
                '"common_proxy_ports":[7897]},"reason":"check proxy"}'
            ),
            "发现残留 Git proxy，已生成清理 Git proxy 的 dry-run 计划。",
        ]
    )
    agent = NetDocAgent(registry=create_default_registry(), llm=llm)

    result = agent.answer("GitHub 代理失败，只检查并给出修复建议", allowed_risk="medium")

    assert result.repair_call is not None
    assert result.repair_call.arguments["action"] == "clear_git_proxy_config"
    assert result.repair_call.arguments["options"]["scope"] == "local"
    assert result.repair_observation is not None
    assert result.repair_observation["metadata"]["executed"] is False
    assert result.unresolved_issues[0]["skill"] == "proxy_vpn_diagnosis"
    assert result.unresolved_issues[0]["repair_plan"]["arguments"]["action"] == (
        "clear_git_proxy_config"
    )


def test_agent_retests_after_executed_repair(monkeypatch) -> None:
    dns_module = __import__("netdoc.skills.dns_diagnosis", fromlist=[""])
    repair_module = __import__("netdoc.skills.repair_actions", fromlist=[""])
    attempts = {"resolve": 0}

    monkeypatch.setattr(
        dns_module,
        "_dns_config_check",
        lambda timeout: {
            "name": "dns_config",
            "success": True,
            "latency_ms": None,
            "evidence": "DNS config readable",
        },
    )

    def resolve_after_repair(domain, timeout):
        attempts["resolve"] += 1
        success = attempts["resolve"] > 1
        return {
            "name": "dns_resolve",
            "success": success,
            "latency_ms": 2,
            "evidence": f"{domain} {'resolved' if success else 'failed'}",
            "details": {"addresses": ["93.184.216.34"]} if success else {},
        }

    monkeypatch.setattr(dns_module, "_resolve_domain", resolve_after_repair)
    monkeypatch.setattr(
        dns_module,
        "_nslookup_check",
        lambda domain, timeout: {
            "name": "nslookup",
            "success": False,
            "latency_ms": 2,
            "evidence": "nslookup failed",
        },
    )
    monkeypatch.setattr(
        dns_module,
        "_direct_public_ip_check",
        lambda domain, ips, port, timeout, use_tls: {
            "name": f"direct_public_ip_{port}",
            "success": True,
            "latency_ms": 3,
            "evidence": "direct IP works",
        },
    )
    monkeypatch.setattr(repair_module, "is_windows", lambda: False)
    monkeypatch.setattr(repair_module, "is_wsl", lambda: False)
    monkeypatch.setattr(repair_module, "is_linux", lambda: True)
    monkeypatch.setattr(
        repair_module,
        "run_command",
        lambda command, *, timeout: CommandResult(
            command=command, returncode=0, stdout="ok", stderr=""
        ),
    )

    llm = FakeLLM(
        responses=[
            '{"skill":"dns_diagnosis","arguments":{"domain":"example.com"},"reason":"check DNS"}',
            "DNS 初诊异常，已刷新缓存并复测正常。",
        ]
    )
    agent = NetDocAgent(registry=create_default_registry(), llm=llm)

    result = agent.answer("检查并修复 DNS", allowed_risk="low", execute_repair=True)

    assert result.repair_observation is not None
    assert result.repair_observation["metadata"]["executed"]
    assert result.retest_observation is not None
    assert result.retest_observation["status"] == "normal"
    assert [step["phase"] for step in result.executed_steps] == [
        "diagnosis",
        "repair",
        "retest",
        "report",
    ]


def test_ask_once_can_print_generated_report(capsys) -> None:
    class FakeResult:
        answer = "DNS 正常。"
        report_observation = {
            "skill": "report_generator",
            "metadata": {
                "report_markdown": "# NetDoc 网络诊断报告\n\n## 每一步证据\n- dns_resolve 通过"
            },
        }

    class FakeAgent:
        def answer(
            self,
            question: str,
            *,
            allowed_risk: str = "none",
            execute_repair: bool = False,
            repair_confirmation: str = "",
        ):
            assert allowed_risk == "medium"
            assert execute_repair
            assert repair_confirmation == "EXECUTE flush_dns_cache"
            return FakeResult()

    exit_code = _ask_once(
        FakeAgent(),
        "example.com 能解析吗？",
        show_json=False,
        show_report=True,
        allowed_risk="medium",
        execute_repair=True,
        repair_confirmation="EXECUTE flush_dns_cache",
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "DNS 正常。" not in captured.out
    assert "# NetDoc 网络诊断报告" in captured.out
    assert "## 每一步证据" in captured.out


def test_ask_once_prints_json_before_final_answer_when_show_json(capsys) -> None:
    class FakeResult:
        answer = "FINAL ANSWER"
        report_observation = None
        unresolved_issues: list[dict[str, object]] = []

        def to_dict(self):
            return {"answer": self.answer, "plan": {"mode": "multi_step"}}

    class FakeAgent:
        def answer(
            self,
            question: str,
            *,
            allowed_risk: str = "none",
            execute_repair: bool = False,
            repair_confirmation: str = "",
        ):
            return FakeResult()

    exit_code = _ask_once(
        FakeAgent(),
        "check",
        show_json=True,
        show_report=False,
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    lines = captured.out.rstrip().splitlines()
    assert lines[0] == "{"
    assert lines[-1] == "FINAL ANSWER"
    assert '"plan"' in captured.out


def test_ask_once_updates_conversation_state() -> None:
    class FakeResult:
        answer = "HTTPS 异常仍未解决。"
        report_observation = None
        unresolved_issues = [
            {
                "skill": "service_connectivity",
                "summary": "HTTPS 端口不可达。",
            }
        ]

        def to_dict(self):
            return {"answer": self.answer, "unresolved_issues": self.unresolved_issues}

    class FakeAgent:
        def answer(
            self,
            question: str,
            *,
            allowed_risk: str = "none",
            execute_repair: bool = False,
            repair_confirmation: str = "",
        ):
            return FakeResult()

    state = ConversationState()
    exit_code = _ask_once(
        FakeAgent(),
        "检查 HTTPS",
        show_json=False,
        show_report=False,
        state=state,
    )

    assert exit_code == 0
    assert state.last_question == "检查 HTTPS"
    assert state.last_answer == "HTTPS 异常仍未解决。"
    assert state.unresolved_issues[0]["skill"] == "service_connectivity"
    assert state.last_result is not None


def test_handle_risk_command_can_show_and_update_risk(capsys) -> None:
    assert _handle_risk_command("/risk", "none") == "none"
    assert _handle_risk_command("/risk medium", "none") == "medium"
    assert _handle_risk_command(":risk high", "medium") == "high"
    assert _handle_risk_command("risk invalid", "low") == "low"
    assert _handle_risk_command("检查 GitHub", "low") is None

    captured = capsys.readouterr()
    assert "当前允许修复风险等级: none" in captured.out
    assert "允许修复风险等级已切换为: medium" in captured.out
    assert "无效风险等级" in captured.out


def test_handle_repair_command(capsys) -> None:
    assert _handle_repair_command("/repair", False) is False
    assert _handle_repair_command("/repair on", False) is True
    assert _handle_repair_command(":repair off", True) is False
    assert _handle_repair_command("repair invalid", True) is True
    assert _handle_repair_command("检查 GitHub", True) is None

    captured = capsys.readouterr()
    assert "当前修复执行模式: dry-run" in captured.out
    assert "修复执行模式已切换为: execute" in captured.out


def test_repair_confirmation_prompt_detects_blocked_medium_action() -> None:
    state = ConversationState(
        last_result={
            "repair_observation": {
                "summary": (
                    "clear_git_proxy_config 未执行：已阻止执行：动作风险 medium "
                    "高于当前自动执行阈值 low，需要用户确认。"
                ),
                "checks": [
                    {
                        "name": "clear_git_proxy_config_plan",
                        "details": {
                            "commands": [
                                ["git", "config", "--local", "--unset", "http.proxy"],
                                ["git", "config", "--local", "--unset", "https.proxy"],
                            ]
                        },
                    }
                ],
                "metadata": {
                    "action": "clear_git_proxy_config",
                    "action_risk": "medium",
                    "execute_requested": True,
                    "executed": False,
                    "confirmation_required": True,
                    "confirmation_phrase": "EXECUTE clear_git_proxy_config",
                },
            }
        }
    )

    prompt = _repair_confirmation_prompt(state)

    assert prompt is not None
    assert prompt["action"] == "clear_git_proxy_config"
    assert prompt["risk"] == "medium"
    assert prompt["confirmation_phrase"] == "EXECUTE clear_git_proxy_config"
    assert prompt["commands"] == [
        ["git", "config", "--local", "--unset", "http.proxy"],
        ["git", "config", "--local", "--unset", "https.proxy"],
    ]


def test_repair_confirmation_prompt_ignores_dry_run() -> None:
    state = ConversationState(
        last_result={
            "repair_observation": {
                "metadata": {
                    "action": "clear_git_proxy_config",
                    "action_risk": "medium",
                    "execute_requested": False,
                    "executed": False,
                    "confirmation_required": True,
                    "confirmation_phrase": "EXECUTE clear_git_proxy_config",
                }
            }
        }
    )

    assert _repair_confirmation_prompt(state) is None


def test_maybe_prompt_repair_confirmation_uses_yes_no_only(monkeypatch, capsys) -> None:
    state = ConversationState(
        last_question="git push 到远程仓库一直执行不了",
        last_result={
            "repair_observation": {
                "summary": (
                    "clear_git_proxy_config 未执行：已阻止执行：动作风险 medium "
                    "高于当前自动执行阈值 low，需要用户确认。"
                ),
                "checks": [
                    {
                        "details": {
                            "commands": [
                                ["git", "config", "--local", "--unset", "http.proxy"],
                            ]
                        }
                    }
                ],
                "metadata": {
                    "action": "clear_git_proxy_config",
                    "action_risk": "medium",
                    "execute_requested": True,
                    "executed": False,
                    "confirmation_required": True,
                    "confirmation_phrase": "EXECUTE clear_git_proxy_config",
                },
            }
        },
    )
    seen: dict[str, object] = {}

    class FakeResult:
        answer = "Git proxy 已清理，复测正常。"
        report_observation = None
        unresolved_issues: list[dict[str, object]] = []

        def to_dict(self):
            return {"answer": self.answer, "unresolved_issues": []}

    class FakeAgent:
        def answer(
            self,
            question: str,
            *,
            allowed_risk: str = "none",
            execute_repair: bool = False,
            repair_confirmation: str = "",
        ):
            seen["question"] = question
            seen["allowed_risk"] = allowed_risk
            seen["execute_repair"] = execute_repair
            seen["repair_confirmation"] = repair_confirmation
            return FakeResult()

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")

    _maybe_prompt_repair_confirmation(
        FakeAgent(),
        state,
        show_json=False,
        show_report=False,
        allowed_risk="low",
        execute_repair=True,
    )

    captured = capsys.readouterr()
    assert "是否执行该修复？输入 yes 执行，no 拒绝" not in captured.out
    assert "确认词" not in captured.out
    assert "已确认执行。" in captured.out
    assert seen == {
        "question": "git push 到远程仓库一直执行不了",
        "allowed_risk": "low",
        "execute_repair": True,
        "repair_confirmation": "EXECUTE clear_git_proxy_config",
    }
    assert state.last_answer == "Git proxy 已清理，复测正常。"


def test_repair_planned_commands_reads_check_details() -> None:
    assert _repair_planned_commands(
        {
            "checks": [
                {"details": {"commands": [["cmd1"], ["cmd2"]]}},
                {"details": {"commands": [["cmd3"]]}},
            ]
        }
    ) == [["cmd1"], ["cmd2"], ["cmd3"]]


def test_continue_command_builds_followup_from_unresolved_issues(capsys) -> None:
    state = ConversationState(
        last_question="检查 GitHub",
        last_answer="DNS 已修复，但 HTTPS 仍异常。",
        unresolved_issues=[
            {
                "skill": "service_connectivity",
                "summary": "HTTPS 端口不可达。",
            }
        ],
    )

    question = _handle_continue_command("/continue 优先检查代理", state)

    assert question is not None
    assert "继续处理上一轮 NetDoc 未解决项" in question
    assert "service_connectivity" in question
    assert "优先检查代理" in question
    captured = capsys.readouterr()
    assert "继续处理 1 个未解决项" in captured.out


def test_continue_command_reports_empty_state(capsys) -> None:
    state = ConversationState()

    assert _handle_continue_command("/continue", state) == ""

    captured = capsys.readouterr()
    assert "当前没有未解决项" in captured.out


def test_issues_command_lists_and_clears_state(capsys) -> None:
    state = ConversationState(
        unresolved_issues=[
            {
                "skill": "service_connectivity",
                "summary": "HTTPS 端口不可达。",
            }
        ]
    )

    assert _handle_issues_command("/issues", state)
    assert _handle_issues_command("/issues clear", state)
    assert state.unresolved_issues == []
    assert _handle_issues_command("检查 GitHub", state) is None

    captured = capsys.readouterr()
    assert "service_connectivity" in captured.out
    assert "已清空未解决项" in captured.out


def test_build_continue_question_includes_previous_context() -> None:
    state = ConversationState(
        last_question="检查 DNS",
        last_answer="DNS 缓存已刷新。",
        unresolved_issues=[{"skill": "proxy_vpn_diagnosis", "summary": "代理异常。"}],
    )

    question = _build_continue_question(state)

    assert "检查 DNS" in question
    assert "DNS 缓存已刷新" in question
    assert "proxy_vpn_diagnosis" in question
