# NetDoc

NetDoc is a lightweight network diagnosis and self-healing agent for personal computers.

The project is designed for the course project of **Large Language Models and Information Decision**. It does not use mature agent runtimes such as Claude Code or Codex as the final runtime. Instead, it builds a small Python-based agent framework where network diagnostic capabilities are exposed as JSON-described skills.

## Project Goal

NetDoc helps users diagnose common network problems such as slow connections, DNS failures, VPN timeouts, proxy misconfiguration, routing conflicts, and service-specific connectivity failures.

The intended workflow is:

1. Understand a user's natural-language network problem.
2. Select relevant diagnostic skills.
3. Execute Python/system-command based checks.
4. Return structured JSON observations.
5. Use an LLM to synthesize diagnosis and repair plans.
6. Execute low-risk repairs or ask for confirmation before high-risk actions.
7. Verify the result and generate a readable report.

## Planned Skill Areas

- `link_status`: adapter status, IP address, DHCP, gateway reachability.
- `dns_diagnosis`: DNS server, domain resolution, DNS latency and cache issues.
- `routing_diagnosis`: default route, multi-adapter conflicts, VPN route residue.
- `proxy_vpn_diagnosis`: system proxy, proxy ports, VPN process and virtual adapter status.
- `service_connectivity`: host, port and protocol checks for services such as GitHub, SSH and HTTPS.
- `network_quality`: latency, packet loss, jitter and bandwidth-oriented checks.

## Repository Layout

```text
src/netdoc/
  core/       Agent loop, skill registry and shared data structures.
  skills/     Network diagnostic skill implementations.
  utils/      Command execution, JSON helpers and platform helpers.
tests/        Unit tests and fixture data.
docs/         Course report notes, diagrams and design documents.
examples/     Example diagnostic requests and JSON outputs.
scripts/      Local development and demo scripts.
```

## Development Status

The repository is currently in the initialization stage. The first milestone is to implement the skill registry and one end-to-end diagnostic path, likely DNS or service connectivity, before expanding to the full six-skill design.
