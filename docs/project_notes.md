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
5. The CLI can show both the final answer and intermediate plan/observation JSON.

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
- First real Skill: `service_connectivity`.
- LLM-backed controller: planner, agent and synthesizer.
- CLI entry points:
  - `scripts/run_service_connectivity.py`
  - `scripts/ask_agent.py`

## Test Policy

Future validation should include two layers:

1. Scripted tests: unit or flow tests for deterministic behavior, using fake LLM responses when testing agent control flow.
2. Real interaction tests: run the CLI against realistic user questions and inspect `--show-json` output to verify the selected Skill, arguments, observation and final answer.

Validated real interaction:

```text
Question: github连接不上
Result: GitHub is currently reachable; DNS, HTTPS and SSH checks are normal.
```
