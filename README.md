# NetDoc

NetDoc 是一个面向个人电脑的轻量级网络诊断与自愈智能体。

本项目用于《大语言模型与信息决策》课程项目。NetDoc 不把 Claude Code、Codex 等成熟代码智能体作为最终运行时，而是使用 Python 自行搭建一个小型智能体框架：网络诊断能力被封装为多个 Skill，每个 Skill 通过 JSON Schema 描述输入输出，并由 Python 代码或系统命令完成真实检测。

## 项目目标

NetDoc 旨在帮助用户诊断常见网络问题，例如网速过慢、DNS 解析失败、VPN 超时、代理配置错误、路由冲突，以及 GitHub、SSH、HTTPS 等特定服务无法连接。

系统的目标流程如下：

1. 理解用户的自然语言网络问题。
2. 根据问题选择相关诊断 Skill。
3. 执行 Python 或系统命令完成网络检查。
4. 返回结构化 JSON 观测结果。
5. 使用大语言模型综合证据，生成诊断结论和修复计划。
6. 调用 `report_generator` 汇总用户问题、Skill 调用、证据和结论，生成展示报告。
7. 自动执行低风险修复，或在高风险操作前请求用户确认。
8. 复测网络状态并更新可读诊断报告。

## 计划中的 Skill

- `link_status`：检查网卡状态、IP 地址、DHCP、默认网关和局域网连通性。
- `dns_diagnosis`：检查 DNS 服务器、域名解析、DNS 延迟和缓存问题。
- `routing_diagnosis`：检查默认路由、多网卡冲突、VPN 路由残留和路由优先级。
- `proxy_vpn_diagnosis`：检查系统代理、代理端口、VPN 进程和虚拟网卡状态。
- `service_connectivity`：检查特定主机、端口和协议，例如 GitHub、SSH、HTTPS。
- `network_quality`：检查延迟、丢包、抖动和带宽相关指标。
- `repair_actions`：执行低风险修复动作，例如刷新 DNS、重启代理或关闭残留系统代理。
- `report_generator`：生成诊断报告，记录证据、结论、修复动作和复测结果。

## 仓库结构

```text
src/netdoc/
  core/       智能体循环、Skill 注册表和共享数据结构。
  skills/     网络诊断 Skill 的具体实现。
  utils/      命令执行、JSON 处理和平台相关工具。
tests/        单元测试和测试夹具。
docs/         课程报告笔记、图示和设计文档。
examples/     示例诊断请求和 JSON 输出。
scripts/      本地开发和演示脚本。
```

## 当前状态

项目已完成第一个可演示里程碑：`link_status`、`routing_diagnosis`、
`service_connectivity`、`dns_diagnosis`、`proxy_vpn_diagnosis`、
`network_quality`、`report_generator` Skill 和
`repair_actions` Skill 以及 LLM-backed agent 命令行入口已经打通。当前系统可以从自然语言问题开始，由 LLM
规划 Skill 调用，执行真实 DNS/TCP/HTTPS 检查，再由 LLM 基于 JSON observation
生成中文诊断结论。

已实现模块：

- `core.skill`：Skill 抽象和轻量 JSON Schema 输入校验。
- `core.registry`：Skill 注册、查找和 schema 导出。
- `core.llm`：Anthropic Messages 兼容 LLM 客户端，从环境变量读取配置。
- `core.planner`：LLM 规划单次 Skill 调用。
- `core.agent`：用户输入、LLM JSON plan、诊断 Skill 调用、observation 收集、LLM 诊断回答和 `report_generator` 收尾报告的最小控制流。
- `utils.command`：统一系统命令执行结果，包含 timeout、返回码、stdout、stderr。
- `utils.platform`：Windows、Linux、WSL 平台判断。
- `utils.json_types`：统一 observation/check JSON 结构。
- `skills.link_status`：检查网卡启用状态、可用 IP、默认网关和网关可达性，WSL 中会用邻居表辅助判断禁 ICMP 的网关。
- `skills.routing_diagnosis`：检查默认路由、多活跃网卡、VPN/虚拟网卡、WSL 网关和异常默认路由优先级。
- `skills.service_connectivity`：检查目标主机 DNS、TCP 端口和 HTTPS/TLS 连通性。
- `skills.dns_diagnosis`：检查当前 DNS 配置、域名解析耗时、解析结果 IP，以及直接公网 IP 访问对比。
- `skills.proxy_vpn_diagnosis`：检查代理环境变量、Git proxy、系统代理、常见代理端口、GitHub 代理访问和 Clash/VPN 进程。
- `skills.network_quality`：通过 ping 检查多个目标的平均延迟、丢包率，并给出目标质量排序。
- `skills.repair_actions`：按 none/low/medium/high 风险等级规划或执行修复动作，默认 dry-run；动作风险高于当前风险等级时必须显式确认。
- `skills.report_generator`：把用户问题、Skill 调用、证据、结论、修复动作、复测结果和遗留问题汇总成课程展示报告。

## 本地运行

安装开发依赖：

```sh
python -m pip install -e ".[dev]"
```

配置 LLM：

```sh
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_BASE_URL="https://cc.580ai.net"
export ANTHROPIC_MODEL="your-model"
```

运行 agent：

```sh
python scripts/ask_agent.py "GitHub 连不上是 DNS 问题、HTTPS 问题，还是 SSH 问题？" --show-json
```

`--show-json` 会展示当前 agent loop 的结构化结果；启用时先输出 JSON，最后再输出简短诊断答案或报告，避免长 JSON 把最终结论顶出可视区域：

- `plan.mode` 为 `multi_step`，`plan.candidate_skills` 是 LLM 选择的候选诊断 Skill 列表。
- `observations` 是按顺序收集到的诊断结果；`observation` 保留第一条诊断结果，兼容旧展示。
- 如果诊断 observation 显示异常，agent 会选择一个可安全映射的 `repair_actions` 修复计划。
- `repair_observation` 记录修复动作是 dry-run、被风险门禁阻止，还是已执行。
- `retest_observation` 仅在修复真实执行后出现，用原诊断 Skill 复测。
- `report_observation` 是 agent 在诊断后自动调用 `report_generator` 生成的收尾报告。
- `executed_steps` 记录实际执行步骤，包括诊断、修复、复测和报告步骤。

当前策略会先执行候选诊断列表，收集所有候选异常，再只自动尝试一个可映射修复动作；
其他异常会进入 `unresolved_issues`，避免一发现问题就停止导致漏诊。

课程展示或报告材料准备时，可以直接打印 `report_generator` 的 Markdown 报告：

```sh
python scripts/ask_agent.py "GitHub 通过代理访问失败，检查 Git proxy、系统代理、Clash 端口和 VPN 进程" --show-report
```

使用 `--show-report` 时，终端只打印报告正文，不重复打印简短诊断答案。

涉及 `repair_actions` 时，`ask_agent.py` 默认只允许 `none` 风险等级。可以用
`--allowed-risk` 设置当前会话允许的最高修复风险。默认仍是 dry-run；如果要允许真实修复，
还需要加 `--execute-repair`。动作风险小于等于当前风险等级时可自动执行；动作风险高于
当前风险等级时，交互模式会要求用户用 `yes` 或 `no` 确认；非交互模式不会越权执行：

```sh
python scripts/ask_agent.py "帮我生成刷新 DNS 缓存的修复计划" --allowed-risk low --show-json
python scripts/ask_agent.py "检查 DNS，确认有问题后刷新缓存" --allowed-risk low --execute-repair --show-json
```

也可以进入交互模式：

```sh
python scripts/ask_agent.py --show-json
```

交互模式中可输入 `/risk` 查看当前风险等级，或输入 `/risk none`、`/risk low`、
`/risk medium`、`/risk high` 动态切换。输入 `/repair on|off` 切换真实执行或
dry-run。如果交互模式中已经开启 `/repair on`，但动作风险高于当前风险等级，
CLI 会展示动作、风险等级和待执行命令，然后等待输入 `yes` 或 `no`；输入 `yes`
后会重跑本轮并执行该次确认的修复。

如果上一轮结果里还有 `unresolved_issues`，交互模式可以继续处理：

```text
/issues
/continue
/continue 优先检查代理和路由
/issues clear
```

`/continue` 会把上一轮问题、回答和未解决项作为上下文传给 agent，继续规划后续诊断；
该状态只保存在当前交互进程内，不写入磁盘。
如果上一轮只是 dry-run 或因风险确认不足而没有真实修复，原异常会继续保留在
`unresolved_issues`，因此可以先检查，再输入 `/repair on`，最后用
`/continue 帮我修复` 接着处理；若修复风险高于当前风险等级，交互界面会再次用
`yes`/`no` 询问是否执行。

单独运行 DNS 诊断 Skill：

```sh
python scripts/run_dns_diagnosis.py github.com --timeout 3
```

单独运行代理/VPN 诊断 Skill：

```sh
python scripts/run_proxy_vpn_diagnosis.py --timeout 3
```

单独运行修复动作 Skill，默认只生成计划，不修改系统：

```sh
python scripts/run_repair_actions.py inspect_supported_actions
python scripts/run_repair_actions.py flush_dns_cache
python scripts/run_repair_actions.py flush_dns_cache --execute --allowed-risk low
python scripts/run_repair_actions.py clear_git_proxy_config --allowed-risk medium
```

单独运行网络质量诊断 Skill：

```sh
python scripts/run_network_quality.py 223.5.5.5 8.8.8.8 github.com --count 4 --timeout 2
```

网络质量真实交互话术和异常网络 namespace 构造见
`docs/network_quality_validation.md`。

单独运行链路和路由诊断 Skill：

```sh
python scripts/run_link_status.py --timeout 2
python scripts/run_routing_diagnosis.py --timeout 2
```

## 测试方式

后续每个里程碑都按两类测试推进：

1. 测试脚本：用 `pytest` 覆盖 schema 校验、registry、Skill 逻辑和 agent 控制流。
2. 真实场景：通过 `scripts/ask_agent.py` 做交互式验证，确认 LLM 规划、真实网络检查和最终回答一致。

当前已通过的真实场景示例：

```text
用户：github连接不上
NetDoc：GitHub 目前网络连接完全正常，DNS、HTTPS 和 SSH 检查均正常。
```

新增代理/VPN 真实交互场景：

```text
用户：GitHub 通过 Clash/VPN 代理访问失败，检查系统代理、Git proxy、代理端口和 VPN 进程
NetDoc：选择 proxy_vpn_diagnosis，输出环境变量代理、Git proxy、系统代理、常见端口、通过代理访问目标和 Clash/VPN 进程证据。

用户：google.com 通过 Clash/VPN 代理访问失败，帮我检查问题
NetDoc：选择 proxy_vpn_diagnosis，在 Clash 直连或代理路径异常场景下定位代理访问失败证据。

用户：GitHub 通过代理访问失败，检查 Git proxy、系统代理、Clash 端口和 VPN 进程
NetDoc：选择 proxy_vpn_diagnosis，在 Git local proxy 指向未监听端口时定位残留 Git proxy 配置。
```

新增链路和路由真实/隔离环境验证：

```text
用户：帮我检查当前网卡是否启用、IP是否有效、默认网关是否存在和网关是否可达
NetDoc：选择 link_status，输出 adapter_enabled、ip_address_valid、default_gateway_exists 和 gateway_reachable 证据。

用户：检查默认路由、多网卡、VPN虚拟网卡、WSL网关和异常路由优先级
NetDoc：选择 routing_diagnosis，输出 default_route、multiple_active_interfaces、vpn_virtual_interfaces、wsl_gateway 和 route_priority 证据。

隔离环境：通过 Linux network namespace 构造缺省网关、不可达网关、多活跃网卡、重复默认路由 metric 和 tun0 VPN 虚拟网卡场景。
NetDoc：对应输出缺失默认路由、网关不可达、多网卡风险、duplicate_best_default_metric 和 VPN 虚拟网卡证据。
```
