"""Shared JSON-compatible observation structures."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


ObservationStatus = Literal["normal", "abnormal", "error"]
RiskLevel = Literal["none", "low", "medium", "high"]


class CheckResult(TypedDict, total=False):
    """One concrete evidence item produced by a skill."""

    name: str
    success: bool
    latency_ms: int | None
    evidence: str
    details: dict[str, Any]


class Observation(TypedDict, total=False):
    """Normalized skill observation returned to the agent."""

    skill: str
    status: ObservationStatus
    checks: list[CheckResult]
    summary: str
    risk_level: RiskLevel
    metadata: dict[str, Any]


def make_check(
    name: str,
    success: bool,
    evidence: str,
    *,
    latency_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> CheckResult:
    """Create one JSON-compatible check item."""
    check: CheckResult = {
        "name": name,
        "success": success,
        "latency_ms": latency_ms,
        "evidence": evidence,
    }
    if details:
        check["details"] = details
    return check


def make_observation(
    *,
    skill: str,
    status: ObservationStatus,
    checks: list[CheckResult],
    summary: str,
    risk_level: RiskLevel = "none",
    metadata: dict[str, Any] | None = None,
) -> Observation:
    """Create the standard observation envelope used by all skills."""
    observation: Observation = {
        "skill": skill,
        "status": status,
        "checks": checks,
        "summary": summary,
        "risk_level": risk_level,
    }
    if metadata:
        observation["metadata"] = metadata
    return observation
