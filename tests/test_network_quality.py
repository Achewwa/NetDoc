from __future__ import annotations

import pytest

from netdoc.core import SchemaValidationError
from netdoc.skills import network_quality
from netdoc.skills.network_quality import skill
from netdoc.utils.command import CommandResult


def test_network_quality_parses_latency_loss_and_compares_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.network_quality", fromlist=[""])
    outputs = {
        "fast.example": (
            "4 packets transmitted, 4 received, 0% packet loss, time 3004ms\n"
            "rtt min/avg/max/mdev = 12.100/15.250/20.300/3.000 ms\n"
        ),
        "loss.example": (
            "4 packets transmitted, 3 received, 25% packet loss, time 3004ms\n"
            "rtt min/avg/max/mdev = 50.000/80.000/120.000/20.000 ms\n"
        ),
    }

    def fake_run_command(command, *, timeout):
        target = command[-1]
        return CommandResult(command=command, returncode=0, stdout=outputs[target], stderr="")

    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/ping")
    monkeypatch.setattr(module, "run_command", fake_run_command)

    observation = skill.run(
        {"targets": ["fast.example", "loss.example"], "count": 4, "timeout_seconds": 1}
    )

    assert observation["skill"] == "network_quality"
    assert observation["status"] == "abnormal"
    assert observation["summary"] == "检测到丢包目标：loss.example。"
    assert observation["metadata"]["best_target"] == "fast.example"
    assert observation["metadata"]["worst_target"] == "loss.example"
    assert [check["name"] for check in observation["checks"]] == [
        "ping_quality_fast_example",
        "ping_quality_loss_example",
        "target_comparison",
    ]
    assert observation["checks"][0]["details"]["avg_latency_ms"] == 15.25
    assert observation["checks"][1]["details"]["packet_loss_percent"] == 25.0
    assert observation["checks"][2]["details"]["ranking"][0]["target"] == "fast.example"


def test_network_quality_reports_all_targets_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = __import__("netdoc.skills.network_quality", fromlist=[""])

    def fake_run_command(command, *, timeout):
        return CommandResult(
            command=command,
            returncode=1,
            stdout="4 packets transmitted, 0 received, 100% packet loss, time 3050ms\n",
            stderr="",
        )

    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/ping")
    monkeypatch.setattr(module, "run_command", fake_run_command)

    observation = skill.run({"targets": ["down.example"], "count": 4, "timeout_seconds": 1})

    assert observation["status"] == "abnormal"
    assert observation["summary"] == "所有目标 ping 均无响应，网络质量无法正常确认。"
    assert observation["checks"][0]["details"]["received_count"] == 0
    assert observation["checks"][0]["details"]["packet_loss_percent"] == 100.0


def test_network_quality_ranks_unreachable_target_as_worst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__("netdoc.skills.network_quality", fromlist=[""])
    outputs = {
        "fast.example": (
            "2 packets transmitted, 2 received, 0% packet loss, time 1001ms\n"
            "rtt min/avg/max/mdev = 10.000/12.000/14.000/1.000 ms\n"
        ),
        "down.example": "2 packets transmitted, 0 received, 100% packet loss, time 1020ms\n",
    }

    def fake_run_command(command, *, timeout):
        target = command[-1]
        return CommandResult(
            command=command,
            returncode=0 if target == "fast.example" else 1,
            stdout=outputs[target],
            stderr="",
        )

    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/ping")
    monkeypatch.setattr(module, "run_command", fake_run_command)

    observation = skill.run(
        {"targets": ["fast.example", "down.example"], "count": 2, "timeout_seconds": 1}
    )

    assert observation["metadata"]["best_target"] == "fast.example"
    assert observation["metadata"]["worst_target"] == "down.example"
    assert observation["checks"][2]["details"]["ranking"][-1]["received_count"] == 0


def test_network_quality_rejects_invalid_count() -> None:
    with pytest.raises(SchemaValidationError):
        skill.run({"targets": ["github.com"], "count": 0})


def test_skills_init_exports_network_quality() -> None:
    assert network_quality.name == "network_quality"
