"""Default skill registration for NetDoc."""

from __future__ import annotations

from netdoc.core.registry import SkillRegistry

from .dns_diagnosis import skill as dns_diagnosis
from .service_connectivity import skill as service_connectivity


def create_default_registry() -> SkillRegistry:
    """Return a registry containing all implemented skills."""
    registry = SkillRegistry()
    registry.register(dns_diagnosis)
    registry.register(service_connectivity)
    return registry
