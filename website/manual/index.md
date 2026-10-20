# 使用手册

> 这本手册回答"某个功能的全部字段、所有选项、所有边界"。你应该是**正在用 LubanAgent 搭建或改造自己的系统**——用到哪页翻哪页。
>
> 每个功能页统一四段：**是什么 / 为什么需要 / 怎么用 / 边界**。第一次学请走[教程](../tutorial/index.md)，这里假设你已经知道 Agent、Tool、RAG 这些词是什么意思。

## 手册结构

| 分区 | 回答什么问题 |
| --- | --- |
| [快速开始](./getting-started.md) | 从克隆到第一次对话，最短路径 |
| [项目架构](./architecture.md) | 系统分层、领地隔离、一次 Run 的数据流、部署拓扑——四张图 |
| [功能手册](./features/agent-definition.md) | 14 个功能逐一展开：定义 / Prompt 库 / 模型工厂 / Runtime / Tool·MCP / Skill / 知识记忆 / 观测 / 评测 / 成本 / 图编排 / 多 Agent / 工作台 / 内置客服 |
| [API 参考](./api/rest.md) | REST 端点全表、SSE 事件协议与错误码、CLI 命令全表 |
| [改造指南](./customization/overview.md) | 把基座改造成你的业务：写 Tool/Skill、写流程与团队、换模型与后端、接业务系统、跟上游升级 |
| [部署运维](./deploy.md) | Docker 全栈、生产安全清单、观测后端 |

## 怎么查

1. **知道功能名** → 左侧"功能手册"直接点。
2. **知道调用方式**（HTTP / SSE / 命令行）→ "API 参考"。
3. **想改框架默认行为**（换模型、换数据库、自定义鉴权）→ "改造指南"。
4. **撞到不认识的词** → [术语表](./glossary.md)。

## 覆盖范围

本手册覆盖开源核心（F1-F13，Apache-2.0）。消息渠道接入、会话状态机、出站风控属商用渠道层（F14-F16），开源版不含；开源用户可通过框架的 `ChannelConnector` 扩展点自行接入外部渠道（见[接入业务系统](./customization/business-integration.md)）。
