"""Default skill registration for NetDoc."""

from __future__ import annotations

from netdoc.core.registry import SkillRegistry

from .dns_diagnosis import skill as dns_diagnosis
from .link_status import skill as link_status
from .network_quality import skill as network_quality
from .proxy_vpn_diagnosis import skill as proxy_vpn_diagnosis
from .repair_actions import skill as repair_actions
from .report_generator import skill as report_generator
from .routing_diagnosis import skill as routing_diagnosis
from .service_connectivity import skill as service_connectivity


def create_default_registry() -> SkillRegistry:
    """Return a registry containing all implemented skills."""
    registry = SkillRegistry()
    registry.register(link_status)
    registry.register(dns_diagnosis)
    registry.register(routing_diagnosis)
    registry.register(proxy_vpn_diagnosis)
    registry.register(network_quality)
    registry.register(repair_actions)
    registry.register(report_generator)
    registry.register(service_connectivity)
    return registry
