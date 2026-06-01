# Network Quality Validation

This note collects real interaction prompts and an isolated abnormal network setup
for the `network_quality` milestone.

## Real Interaction Prompts

Baseline multi-target comparison:

```sh
python scripts/ask_agent.py "帮我比较 223.5.5.5、8.8.8.8 和 github.com 的 ping 延迟和丢包率" --show-json
```

Expected behavior:

- Planner selects `network_quality`.
- Arguments include `targets` for the three requested hosts.
- Observation contains one `ping_quality_*` check per target and one
  `target_comparison` check.
- Final answer explains average latency, packet loss and best/worst target.

Default quality check:

```sh
python scripts/ask_agent.py "我感觉现在网络很慢，帮我看一下延迟和丢包情况" --show-json
```

Expected behavior:

- Planner selects `network_quality`.
- If the planner omits targets, the skill uses default targets:
  `223.5.5.5`, `8.8.8.8`, `github.com`.
- Final answer should distinguish local/public DNS-like targets from remote targets
  when the ranking shows a clear gap.

Packet-loss-focused prompt:

```sh
python scripts/ask_agent.py "网页偶尔打不开，帮我确认是不是 ping 丢包导致的" --show-json
```

Expected behavior:

- Planner selects `network_quality`.
- Final answer cites `packet_loss_percent` from checks rather than guessing.

Scope-control prompt for future features:

```sh
python scripts/ask_agent.py "先不要做 speedtest，只检查 ping 延迟、丢包率，并比较多个目标" --show-json
```

Expected behavior:

- Planner selects `network_quality`.
- Final answer does not claim download speed, upload speed or jitter evidence.

## Abnormal Network Namespace

Use the helper script to create an isolated namespace. It does not modify the main
network namespace except for a temporary veth interface named `ndq-host`.

```sh
sudo scripts/network_quality_abnormal_env.sh setup
sudo scripts/network_quality_abnormal_env.sh run
sudo scripts/network_quality_abnormal_env.sh cleanup
```

The environment provides two deterministic targets:

- `10.200.0.1`: reachable veth peer. When `tc` is installed, replies are delayed
  by `NETDOC_QUALITY_DELAY_MS`, default `220 ms`, so latency should exceed the
  current `network_quality` normal threshold.
- `10.200.0.254`: no peer in the same subnet, so ping should show 100% packet loss.

Expected `run` result:

- `status` is `abnormal`.
- `10.200.0.1` has received replies and elevated average latency.
- `10.200.0.254` has `received_count = 0` and `packet_loss_percent = 100.0`.
- `target_comparison` ranks `10.200.0.1` as best and `10.200.0.254` as worst.

To make the reachable target more or less delayed:

```sh
sudo NETDOC_QUALITY_DELAY_MS=350 scripts/network_quality_abnormal_env.sh setup
sudo scripts/network_quality_abnormal_env.sh run
sudo scripts/network_quality_abnormal_env.sh cleanup
```

If a test is interrupted, cleanup is idempotent:

```sh
sudo scripts/network_quality_abnormal_env.sh cleanup
```
