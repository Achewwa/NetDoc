# Project Notes

## Candidate Report Title

NetDoc: 基于 Skill 调用的大语言模型网络诊断与自愈智能体

## Current Architecture Idea

NetDoc uses Python skills to collect network evidence and JSON observations to communicate with the agent controller. The LLM is used for planning, synthesis and user-facing explanation rather than for direct system access.

## Skill Set

NetDoc currently plans eight skills:

1. `link_status`
2. `dns_diagnosis`
3. `routing_diagnosis`
4. `proxy_vpn_diagnosis`
5. `service_connectivity`
6. `network_quality`
7. `repair_actions`
8. `report_generator`
