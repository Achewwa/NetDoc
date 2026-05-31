from __future__ import annotations

import pytest

from netdoc.core import SchemaValidationError
from netdoc.skills.proxy_vpn_diagnosis import skill as proxy_skill
from netdoc.skills.report_generator import skill as report_skill
from netdoc.utils.command import CommandResult
from netdoc.utils.json_types import make_check, make_observation


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_proxy_vpn_diagnosis_flags_stale_local_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    monkeypatch.setattr(
        module,
        "_git_proxy_check",
        lambda timeout: (make_check("git_proxy_config", True, "no git proxy"), []),
    )
    monkeypatch.setattr(
        module,
        "_system_proxy_check",
        lambda timeout: (make_check("system_proxy_config", True, "no system proxy"), []),
    )
    monkeypatch.setattr(
        module,
        "_port_probe_result",
        lambda port, timeout: {"listening": False, "attempts": []},
    )
    monkeypatch.setattr(
        module,
        "_github_via_proxy_check",
        lambda target_url, endpoints, timeout: make_check(
            "github_via_proxy", False, "proxy failed"
        ),
    )
    monkeypatch.setattr(
        module,
        "_proxy_vpn_process_check",
        lambda timeout: make_check("clash_vpn_processes", True, "no process"),
    )

    observation = proxy_skill.run(
        {"target_url": "https://github.com", "common_proxy_ports": [7897]}
    )

    assert observation["skill"] == "proxy_vpn_diagnosis"
    assert observation["status"] == "abnormal"
    assert observation["summary"] == (
        "代理配置指向本机端口，但对应端口未监听，疑似残留代理配置或代理客户端未启动。"
    )
    assert observation["metadata"]["configured_proxy_endpoints"][0]["port"] == 7897
    assert [check["name"] for check in observation["checks"]] == [
        "proxy_environment_variables",
        "git_proxy_config",
        "system_proxy_config",
        "common_proxy_ports",
        "github_via_proxy",
        "clash_vpn_processes",
    ]


def test_proxy_vpn_diagnosis_is_normal_without_proxy_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(
        module,
        "_git_proxy_check",
        lambda timeout: (make_check("git_proxy_config", True, "no git proxy"), []),
    )
    monkeypatch.setattr(
        module,
        "_system_proxy_check",
        lambda timeout: (make_check("system_proxy_config", True, "no system proxy"), []),
    )
    monkeypatch.setattr(
        module,
        "_port_probe_result",
        lambda port, timeout: {"listening": False, "attempts": []},
    )
    monkeypatch.setattr(
        module,
        "_github_via_proxy_check",
        lambda target_url, endpoints, timeout: make_check("github_via_proxy", True, "skipped"),
    )
    monkeypatch.setattr(
        module,
        "_proxy_vpn_process_check",
        lambda timeout: make_check("clash_vpn_processes", True, "no process"),
    )

    observation = proxy_skill.run({"common_proxy_ports": [7890, 8080]})

    assert observation["status"] == "normal"
    assert observation["metadata"]["configured_proxy_endpoints"] == []


def test_proxy_vpn_diagnosis_skips_wsl_windows_system_proxy_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])
    _clear_proxy_env(monkeypatch)
    monkeypatch.setattr(
        module,
        "_git_proxy_check",
        lambda timeout: (make_check("git_proxy_config", True, "no git proxy"), []),
    )
    monkeypatch.setattr(
        module,
        "_system_proxy_check",
        lambda timeout: (
            make_check("system_proxy_config", True, "windows proxy"),
            [
                {
                    "source": "system",
                    "name": "system.proxy_server_0",
                    "value": "127.0.0.1:7890",
                    "url": "http://127.0.0.1:7890",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": 7890,
                    "diagnostic_scope": "windows_host",
                }
            ],
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
        lambda target_url, endpoints, timeout: make_check(
            "github_via_proxy",
            True,
            "未发现可用于测试的代理配置，跳过通过代理访问 GitHub。",
        ),
    )
    monkeypatch.setattr(
        module,
        "_proxy_vpn_process_check",
        lambda timeout: make_check("clash_vpn_processes", True, "no process"),
    )

    observation = proxy_skill.run({"common_proxy_ports": [7890]})
    port_check = observation["checks"][3]

    assert observation["status"] == "normal"
    assert observation["summary"] == (
        "仅发现当前运行环境不可直接测试的系统代理配置，未发现 WSL 本机代理端口或进程异常。"
    )
    assert port_check["success"]
    assert port_check["details"]["configured_local_ports"] == []
    assert port_check["details"]["skipped_local_endpoints"][0]["diagnostic_scope"] == "windows_host"


def test_proxy_vpn_diagnosis_skips_windows_host_proxy_for_wsl_target_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])

    def fail_run_command(*args, **kwargs):
        raise AssertionError("curl should not run for Windows-host-only proxy endpoints")

    monkeypatch.setattr(module, "run_command", fail_run_command)

    check = module._github_via_proxy_check(
        "https://github.com",
        [
            {
                "source": "system",
                "name": "system.proxy_server_0",
                "url": "http://127.0.0.1:7890",
                "host": "127.0.0.1",
                "port": 7890,
                "diagnostic_scope": "windows_host",
            }
        ],
        1.0,
    )

    assert check["success"]
    assert "跳过" in check["evidence"]


def test_proxy_vpn_diagnosis_parses_git_proxy_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])

    def fake_run_command(command: list[str], *, timeout: float) -> CommandResult:
        if "--global" in command:
            return CommandResult(command, 0, "http.proxy http://127.0.0.1:7890\n", "")
        return CommandResult(command, 1, "", "")

    monkeypatch.setattr(module, "run_command", fake_run_command)

    check, endpoints = module._git_proxy_check(1.0)

    assert check["success"]
    assert endpoints == [
        {
            "source": "git",
            "name": "global.http.proxy",
            "value": "http://127.0.0.1:7890",
            "url": "http://127.0.0.1:7890",
            "scheme": "http",
            "host": "127.0.0.1",
            "port": 7890,
        }
    ]


def test_proxy_vpn_process_check_ignores_keyword_in_python_arguments() -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])
    stdout = "\n".join(
        [
            "    PID COMMAND         COMMAND",
            " 157525 python          python scripts/ask_agent.py check Clash proxy",
            " 157526 conda           conda run python scripts/ask_agent.py VPN",
        ]
    )

    assert module._find_process_matches(stdout) == []


def test_proxy_vpn_process_check_matches_real_proxy_binary() -> None:
    module = __import__("netdoc.skills.proxy_vpn_diagnosis", fromlist=[""])
    stdout = "\n".join(
        [
            "    PID COMMAND         COMMAND",
            " 157700 clash           /usr/bin/clash -d /tmp/profile",
            " 157701 mihomo          /usr/local/bin/mihomo -f config.yaml",
        ]
    )

    matches = module._find_process_matches(stdout)

    assert [match["keyword"] for match in matches] == ["clash", "mihomo"]


def test_proxy_vpn_diagnosis_rejects_invalid_ports() -> None:
    with pytest.raises(SchemaValidationError):
        proxy_skill.run({"common_proxy_ports": [7890, 7890]})


def test_report_generator_builds_course_friendly_markdown() -> None:
    observation = make_observation(
        skill="proxy_vpn_diagnosis",
        status="abnormal",
        checks=[
            make_check(
                "common_proxy_ports",
                False,
                "代理配置指向本机端口，但端口未监听: 7897",
            )
        ],
        summary="代理配置残留。",
    )

    report = report_skill.run(
        {
            "user_question": "GitHub 通过代理访问失败",
            "skill_calls": [
                {
                    "skill": "proxy_vpn_diagnosis",
                    "arguments": {"target_url": "https://github.com"},
                    "reason": "检查代理/VPN 状态",
                }
            ],
            "observations": [observation],
            "repair_actions": [{"action": "清理 https_proxy", "result": "已移除残留环境变量"}],
            "retest_results": [{"name": "GitHub 代理复测", "result": "恢复成功"}],
            "unresolved_issues": [],
        }
    )

    markdown = report["metadata"]["report_markdown"]
    assert report["skill"] == "report_generator"
    assert report["status"] == "abnormal"
    assert "## 用户问题" in markdown
    assert "## 调用了哪些 skill" in markdown
    assert "proxy_vpn_diagnosis" in markdown
    assert "common_proxy_ports [失败]" in markdown


def test_report_generator_rejects_missing_observations() -> None:
    with pytest.raises(SchemaValidationError):
        report_skill.run({"user_question": "GitHub 失败"})
