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

项目目前处于初始化阶段。第一个里程碑是实现 Skill 注册机制和一条端到端诊断路径，优先考虑 DNS 诊断或服务连通性诊断，然后再扩展到完整的六类 Skill。
