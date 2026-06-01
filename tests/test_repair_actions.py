from __future__ import annotations

from pathlib import Path

import pytest

from netdoc.core import SchemaValidationError
from netdoc.skills import create_default_registry
from netdoc.skills.repair_actions import skill
from netdoc.utils.command import CommandResult


def test_repair_actions_lists_supported_actions() -> None:
    observation = skill.run({"action": "inspect_supported_actions"})

    assert observation["skill"] == "repair_actions"
    assert observation["status"] == "normal"
    assert observation["risk_level"] == "none"
    assert not observation["metadata"]["executed"]
    assert observation["metadata"]["handler_ran"]
    actions = observation["checks"][2]["details"]["actions"]
    risk_by_action = {item["name"]: item["risk"] for item in actions}
    assert risk_by_action["flush_dns_cache"] == "low"
    assert risk_by_action["clear_git_proxy_config"] == "medium"
    assert risk_by_action["disable_system_proxy"] == "medium"
    assert risk_by_action["restart_network_interface"] == "high"


def test_low_risk_dns_flush_dry_run_does_not_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])

    def fail_run_command(*args, **kwargs):
        raise AssertionError("dry-run must not execute commands")

    monkeypatch.setattr(module, "run_command", fail_run_command)
    monkeypatch.setattr(module, "is_windows", lambda: False)
    monkeypatch.setattr(module, "is_wsl", lambda: False)
    monkeypatch.setattr(module, "is_linux", lambda: True)

    observation = skill.run({"action": "flush_dns_cache"})

    assert observation["status"] == "normal"
    assert observation["risk_level"] == "low"
    assert not observation["metadata"]["executed"]
    assert observation["checks"][2]["name"] == "flush_dns_cache_plan"
    assert observation["checks"][2]["details"]["executed"] is False


def test_low_risk_dns_flush_executes_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, timeout: float) -> CommandResult:
        calls.append(command)
        return CommandResult(command=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(module, "run_command", fake_run_command)
    monkeypatch.setattr(module, "is_windows", lambda: False)
    monkeypatch.setattr(module, "is_wsl", lambda: False)
    monkeypatch.setattr(module, "is_linux", lambda: True)

    observation = skill.run(
        {"action": "flush_dns_cache", "execute": True, "allowed_risk": "low"}
    )

    assert observation["status"] == "normal"
    assert observation["metadata"]["executed"]
    assert calls == [["resolvectl", "flush-caches"]]
    assert observation["checks"][2]["name"] == "flush_dns_cache"


def test_low_risk_dns_flush_requires_confirmation_above_allowed_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])

    def fail_run_command(*args, **kwargs):
        raise AssertionError("above-threshold action must not execute without confirmation")

    monkeypatch.setattr(module, "run_command", fail_run_command)

    observation = skill.run(
        {
            "action": "flush_dns_cache",
            "execute": True,
            "allowed_risk": "none",
        }
    )

    assert observation["status"] == "normal"
    assert not observation["metadata"]["executed"]
    assert observation["metadata"]["confirmation_phrase"] == "EXECUTE flush_dns_cache"
    assert "高于当前自动执行阈值 none" in observation["summary"]


def test_medium_risk_action_requires_confirmation_above_allowed_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])

    def fail_run_command(*args, **kwargs):
        raise AssertionError("blocked action must not execute commands")

    monkeypatch.setattr(module, "run_command", fail_run_command)

    observation = skill.run(
            {
                "action": "disable_system_proxy",
                "execute": True,
                "allowed_risk": "low",
            }
        )

    assert observation["status"] == "normal"
    assert not observation["metadata"]["executed"]
    assert observation["metadata"]["confirmation_phrase"] == "EXECUTE disable_system_proxy"
    assert "需要用户确认" in observation["summary"]
    assert observation["checks"][2]["details"]["blocked"]


def test_clear_git_proxy_config_dry_run_records_commands() -> None:
    observation = skill.run(
        {
            "action": "clear_git_proxy_config",
            "options": {"scope": "local", "cwd": "/tmp/project"},
        }
    )

    assert observation["status"] == "normal"
    assert observation["risk_level"] == "medium"
    assert not observation["metadata"]["executed"]
    assert observation["checks"][2]["name"] == "clear_git_proxy_config_plan"
    assert observation["checks"][2]["details"]["cwd"] == "/tmp/project"
    assert observation["checks"][2]["details"]["commands"] == [
        ["git", "config", "--local", "--unset", "http.proxy"],
        ["git", "config", "--local", "--unset", "https.proxy"],
    ]


def test_clear_git_proxy_config_executes_when_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])
    calls: list[tuple[list[str], str | None]] = []

    def fake_run_command(
        command: list[str],
        *,
        timeout: float,
        cwd: str | None = None,
        **kwargs,
    ) -> CommandResult:
        calls.append((command, cwd))
        return CommandResult(command=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "run_command", fake_run_command)

    observation = skill.run(
        {
            "action": "clear_git_proxy_config",
            "execute": True,
            "allowed_risk": "medium",
            "options": {"scope": "local", "cwd": "/tmp/project"},
        }
    )

    assert observation["status"] == "normal"
    assert observation["metadata"]["executed"]
    assert calls == [
        (["git", "config", "--local", "--unset", "http.proxy"], "/tmp/project"),
        (["git", "config", "--local", "--unset", "https.proxy"], "/tmp/project"),
    ]


def test_high_risk_dry_run_validates_options_and_records_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])
    monkeypatch.setattr(module, "is_windows", lambda: False)
    monkeypatch.setattr(module, "is_wsl", lambda: False)

    observation = skill.run(
        {
            "action": "set_dns_servers",
            "options": {"interface": "eth0", "servers": ["223.5.5.5", "1.1.1.1"]},
        }
    )

    assert observation["status"] == "normal"
    assert observation["risk_level"] == "high"
    assert not observation["metadata"]["executed"]
    assert observation["checks"][2]["name"] == "set_dns_servers_plan"
    assert observation["checks"][2]["details"]["commands"] == [
        ["resolvectl", "dns", "eth0", "223.5.5.5", "1.1.1.1"]
    ]


def test_high_risk_execute_requires_confirmation_above_allowed_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.repair_actions", fromlist=[""])

    def fail_run_command(*args, **kwargs):
        raise AssertionError("unconfirmed action must not execute commands")

    monkeypatch.setattr(module, "run_command", fail_run_command)

    observation = skill.run(
        {
            "action": "restart_network_interface",
            "execute": True,
            "allowed_risk": "medium",
            "options": {"interface": "eth0"},
        }
    )

    assert observation["status"] == "normal"
    assert not observation["metadata"]["executed"]
    assert "高于当前自动执行阈值 medium" in observation["summary"]


def test_clear_temp_cache_rejects_unsafe_paths() -> None:
    observation = skill.run(
        {
            "action": "clear_temp_cache",
            "execute": True,
            "allowed_risk": "low",
            "options": {"paths": ["/etc"]},
        }
    )

    assert observation["status"] == "abnormal"
    assert not observation["checks"][2]["success"]
    assert "拒绝清理" in observation["checks"][2]["evidence"]


def test_clear_temp_cache_removes_only_netdoc_temp_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "netdoc" / "cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "item.txt"
    cache_file.write_text("cached", encoding="utf-8")

    observation = skill.run(
        {
            "action": "clear_temp_cache",
            "execute": True,
            "allowed_risk": "low",
            "options": {"paths": [str(cache_dir)]},
        }
    )

    assert observation["status"] == "normal"
    assert not cache_dir.exists()
    assert observation["checks"][2]["details"]["removed"] == [str(cache_dir)]


def test_repair_actions_is_registered_and_exported() -> None:
    registry = create_default_registry()

    assert registry.has("repair_actions")
    assert "repair_actions" in registry.names()


def test_repair_actions_rejects_unknown_action() -> None:
    with pytest.raises(SchemaValidationError):
        skill.run({"action": "format_disk"})
