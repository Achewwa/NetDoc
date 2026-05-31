"""Default skill registration for NetDoc."""

from __future__ import annotations

from netdoc.core.registry import SkillRegistry

from .dns_diagnosis import skill as dns_diagnosis
from .proxy_vpn_diagnosis import skill as proxy_vpn_diagnosis
from .report_generator import skill as report_generator
from .service_connectivity import skill as service_connectivity


def create_default_registry() -> SkillRegistry:
    """Return a registry containing all implemented skills."""
    registry = SkillRegistry()
    registry.register(dns_diagnosis)
    registry.register(proxy_vpn_diagnosis)
    registry.register(report_generator)
    registry.register(service_connectivity)
    return registry
