"""Default skill registration for NetDoc."""

from __future__ import annotations

from netdoc.core.registry import SkillRegistry

from .service_connectivity import skill as service_connectivity


def create_default_registry() -> SkillRegistry:
    """Return a registry containing all implemented skills."""
    registry = SkillRegistry()
    registry.register(service_connectivity)
    return registry
