# AGENTS.md

This file keeps project context aligned for Codex or other coding agents working on NetDoc.

## Project Identity

- Project name: NetDoc
- Full topic: A Python-built network diagnosis and self-healing agent based on skill calling and JSON observations.
- Course context: Large Language Models and Information Decision course project.
- Runtime direction: Build our own lightweight agent framework in Python. Do not make Claude Code, Codex, or another mature coding agent the final runtime.

## Core Design

NetDoc should separate responsibilities clearly:

- The agent/controller understands user requests, plans skill calls, maintains memory and asks the LLM for structured decisions.
- Skills perform concrete network checks or repair actions.
- Skills communicate through JSON-compatible dictionaries.
- Rules handle deterministic safety checks and common network conclusions.
- The LLM improves user experience by selecting skills, combining evidence, explaining results and producing repair plans.

## Planned Skills

1. `link_status`: network adapter, IP, DHCP, default gateway and local link checks.
2. `dns_diagnosis`: DNS configuration and domain resolution checks.
3. `routing_diagnosis`: route table, default route, interface priority and VPN route residue checks.
4. `proxy_vpn_diagnosis`: system proxy, proxy process/port and VPN adapter/process checks.
5. `service_connectivity`: host/port/protocol reachability checks.
6. `network_quality`: latency, packet loss, jitter and speed-related checks.
7. `repair_actions`: low-risk repair operations with risk labels, such as flushing DNS, restarting proxy-related processes or clearing stale system proxy settings.
8. `report_generator`: diagnosis report generation for demos and course submission, including evidence, conclusions, repair actions and verification results.

## Engineering Principles

- Prefer small Python modules with clear JSON inputs and outputs.
- Keep platform-specific logic isolated, especially Windows, Linux and WSL commands.
- Use timeouts for all external commands and network probes.
- Never perform high-risk repair actions without explicit confirmation.
- Keep diagnosis evidence in the output so the report can cite concrete observations.
- Avoid hardcoding one user's machine details into reusable logic.

## Git Notes

- Remote repository: `git@github.com:Achewwa/NetDoc.git`
- WSL uses a dedicated GitHub SSH key at `/home/achewwa/.ssh/id_ed25519_netdoc`, selected through `/home/achewwa/.ssh/config`.
- Default working branch convention for Codex-created work: `codex/<short-description>` unless the user requests direct work on `main`.
- Do not commit `assignment.pdf`; it is local course context.
