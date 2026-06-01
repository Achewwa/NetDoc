# Project Notes

## Candidate Report Title

NetDoc: 基于 Skill 调用的大语言模型网络诊断与自愈智能体

## Current Architecture Idea

NetDoc uses Python skills to collect network evidence and JSON observations to communicate with the agent controller. The LLM is used for planning, synthesis and user-facing explanation rather than for direct system access.

Current implemented flow:

1. User asks a natural-language network question.
2. The LLM planner selects one registered Skill and returns JSON arguments.
3. Python executes the Skill and collects JSON-compatible evidence.
4. The LLM synthesizer explains the observation in concise Chinese.
5. The agent calls `report_generator` to create a course-friendly Markdown report
   from the question, skill call, observation and final diagnosis.
6. The CLI can show either the concise answer or the generated report with
   `--show-report`, and intermediate `executed_steps`, plan, observation and report
   JSON with `--show-json`.

Current agent limitation: each turn plans one primary diagnosis skill, then calls
`report_generator` as a deterministic finishing step. The current result shape is:

- `plan.mode`: `single_step`
- `plan.skill`: the single primary diagnosis skill selected by the LLM
- `observation`: the primary diagnosis skill observation
- `report_observation`: the deterministic `report_generator` output
- `executed_steps`: the actual diagnosis and report steps, including each step's
  phase, step number, skill, arguments, reason, observation key, and next action.

Future multi-step diagnosis should switch to `plan.mode = "multi_step"` and return
`observations: []`, where each diagnosis skill has its own arguments, observation,
reason for continuing or stopping, and `observation_key` such as `observations[0]`.

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

## Implemented Milestone

The first end-to-end milestone is complete, and the lower-level link/routing
diagnosis milestone has also been implemented:

- Core abstractions: `Skill`, schema validation, `SkillRegistry`.
- Utilities: command execution, platform detection, shared JSON observation types.
- Real Skills: `link_status`, `routing_diagnosis`, `service_connectivity`,
  `dns_diagnosis`, `proxy_vpn_diagnosis`, `report_generator`.
- LLM-backed controller: planner, agent and synthesizer.
- CLI entry points:
  - `scripts/run_link_status.py`
  - `scripts/run_dns_diagnosis.py`
  - `scripts/run_routing_diagnosis.py`
  - `scripts/run_service_connectivity.py`
  - `scripts/run_proxy_vpn_diagnosis.py`
  - `scripts/ask_agent.py`

`link_status` checks active non-loopback adapters, usable IP addresses, default
gateways and gateway reachability. In WSL, gateway checks treat a failed ICMP ping
plus a valid neighbor-table MAC entry as link-layer reachability evidence so that
firewall or NAT ICMP behavior is not overreported as a broken gateway.

`routing_diagnosis` checks default routes, multiple active interfaces, VPN-like
virtual adapters, WSL gateway reachability and default-route priority problems such
as duplicated best metrics, missing metrics and default routes pointing at disabled
interfaces.

`dns_diagnosis` currently checks the local DNS configuration, resolves a target domain
with Python `socket.getaddrinfo()`, records resolution latency and IP addresses, falls
back to `nslookup` when Python resolution fails, and compares direct access to resolved
public IPs.

## Test Policy

Future validation should include two layers:

1. Scripted tests: unit or flow tests for deterministic behavior, using fake LLM responses when testing agent control flow.
2. Real interaction tests: run the CLI against realistic user questions and inspect `--show-json` output to verify the selected Skill, arguments, observation and final answer.

Validated real interaction:

```text
Question: github连接不上
Result: GitHub is currently reachable; DNS, HTTPS and SSH checks are normal.
```

Additional proxy/VPN interaction validation:

```text
Question: GitHub 通过 Clash/VPN 代理访问失败，帮我检查是不是系统代理、Git proxy、代理端口或 VPN 进程的问题
Expected plan: proxy_vpn_diagnosis
Observed result: The planner selected proxy_vpn_diagnosis. The observation contained
environment proxy, Git proxy, system proxy, common proxy ports, target access through
proxy and Clash/VPN process checks. In the misleading GitHub scenario, GitHub access
through the WSL host proxy succeeded, so the answer correctly treated the reported
failure as not reproduced on the checked path.
```

```text
Question: google.com 通过 Clash/VPN 代理访问失败，帮我检查问题
Expected plan: proxy_vpn_diagnosis
Observed result: The planner selected proxy_vpn_diagnosis. With Clash set to direct
mode or otherwise unable to proxy the target, the target access check failed with
curl connection reset evidence and the final answer localized the proxy path failure.
```

```text
Setup: git config --local http.proxy http://127.0.0.1:7897 and
git config --local https.proxy http://127.0.0.1:7897
Question: GitHub 通过代理访问失败，检查 Git proxy、系统代理、Clash 端口和 VPN 进程
Expected plan: proxy_vpn_diagnosis
Observed result: The planner selected proxy_vpn_diagnosis. The observation captured
local Git proxy entries pointing at 127.0.0.1:7897, detected that the configured port
was not listening, and the final answer identified stale Git proxy configuration.
Cleanup: git config --local --unset http.proxy and git config --local --unset https.proxy.
```

Additional link/routing validation:

```text
Question: 帮我检查当前网卡是否启用、IP是否有效、默认网关是否存在和网关是否可达
Expected plan: link_status
Observed script baseline: `scripts/run_link_status.py --timeout 2` returned normal
status on WSL, with eth0 enabled, a usable 172.22.x.x address, default gateway
172.22.0.1 and gateway reachability inferred from the neighbor table when ICMP did
not reply.
```

```text
Question: 检查默认路由、多网卡、VPN虚拟网卡、WSL网关和异常路由优先级
Expected plan: routing_diagnosis
Observed script baseline: `scripts/run_routing_diagnosis.py --timeout 2` returned
normal status on WSL, with one active interface, no VPN-like interface, a valid
default route and no duplicate best default metric.
```

```text
Setup: Linux network namespace with veth pairs and optional tun0.
Validated abnormal scenarios: missing default gateway, default gateway present but
unreachable, multiple active interfaces, duplicated default-route metric and VPN-like
tun0 virtual adapter.
Expected result: link_status and routing_diagnosis surface the corresponding failed
or evidence-bearing checks while keeping all observations JSON-compatible.
Cleanup: `sudo ip netns del netdoc_test` and remove any remaining host-side veth
devices such as nd-host or nd2-host.
```
