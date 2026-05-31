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
6. The CLI can show the concise answer, the generated report with `--show-report`,
   and intermediate plan/observation/report JSON with `--show-json`.

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

The first end-to-end milestone is complete:

- Core abstractions: `Skill`, schema validation, `SkillRegistry`.
- Utilities: command execution, platform detection, shared JSON observation types.
- Real Skills: `service_connectivity`, `dns_diagnosis`, `proxy_vpn_diagnosis`,
  `report_generator`.
- LLM-backed controller: planner, agent and synthesizer.
- CLI entry points:
  - `scripts/run_dns_diagnosis.py`
  - `scripts/run_service_connectivity.py`
  - `scripts/run_proxy_vpn_diagnosis.py`
  - `scripts/ask_agent.py`

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
