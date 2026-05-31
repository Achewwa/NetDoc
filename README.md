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
6. 自动执行低风险修复，或在高风险操作前请求用户确认。
7. 复测网络状态并生成可读诊断报告。

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

项目已完成第一个可演示里程碑：`service_connectivity`、`dns_diagnosis`、
`proxy_vpn_diagnosis`、`report_generator` Skill 和
LLM-backed agent 命令行入口已经打通。当前系统可以从自然语言问题开始，由 LLM
规划 Skill 调用，执行真实 DNS/TCP/HTTPS 检查，再由 LLM 基于 JSON observation
生成中文诊断结论。

已实现模块：

- `core.skill`：Skill 抽象和轻量 JSON Schema 输入校验。
- `core.registry`：Skill 注册、查找和 schema 导出。
- `core.llm`：Anthropic Messages 兼容 LLM 客户端，从环境变量读取配置。
- `core.planner`：LLM 规划单次 Skill 调用。
- `core.agent`：用户输入、LLM JSON plan、Skill 调用、observation 收集、LLM 诊断报告的最小控制流。
- `utils.command`：统一系统命令执行结果，包含 timeout、返回码、stdout、stderr。
- `utils.platform`：Windows、Linux、WSL 平台判断。
- `utils.json_types`：统一 observation/check JSON 结构。
- `skills.service_connectivity`：检查目标主机 DNS、TCP 端口和 HTTPS/TLS 连通性。
- `skills.dns_diagnosis`：检查当前 DNS 配置、域名解析耗时、解析结果 IP，以及直接公网 IP 访问对比。
- `skills.proxy_vpn_diagnosis`：检查代理环境变量、Git proxy、系统代理、常见代理端口、GitHub 代理访问和 Clash/VPN 进程。
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

也可以进入交互模式：

```sh
python scripts/ask_agent.py --show-json
```

单独运行 DNS 诊断 Skill：

```sh
python scripts/run_dns_diagnosis.py github.com --timeout 3
```

单独运行代理/VPN 诊断 Skill：

```sh
python scripts/run_proxy_vpn_diagnosis.py --timeout 3
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
