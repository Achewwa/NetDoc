"""Network diagnostic skills."""

from .registry import create_default_registry
from .service_connectivity import skill as service_connectivity

__all__ = ["create_default_registry", "service_connectivity"]
