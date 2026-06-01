"""Network diagnostic skills."""

from .dns_diagnosis import skill as dns_diagnosis
from .link_status import skill as link_status
from .network_quality import skill as network_quality
from .proxy_vpn_diagnosis import skill as proxy_vpn_diagnosis
from .repair_actions import skill as repair_actions
from .registry import create_default_registry
from .report_generator import skill as report_generator
from .routing_diagnosis import skill as routing_diagnosis
from .service_connectivity import skill as service_connectivity

__all__ = [
    "create_default_registry",
    "dns_diagnosis",
    "link_status",
    "network_quality",
    "proxy_vpn_diagnosis",
    "repair_actions",
    "report_generator",
    "routing_diagnosis",
    "service_connectivity",
]
