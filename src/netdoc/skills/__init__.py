"""Network diagnostic skills."""

from .dns_diagnosis import skill as dns_diagnosis
from .proxy_vpn_diagnosis import skill as proxy_vpn_diagnosis
from .registry import create_default_registry
from .report_generator import skill as report_generator
from .service_connectivity import skill as service_connectivity

__all__ = [
    "create_default_registry",
    "dns_diagnosis",
    "proxy_vpn_diagnosis",
    "report_generator",
    "service_connectivity",
]
