# 使用手册

这里按功能介绍 LubanAgent 的配置、调用方式和限制，用到哪一页就查哪一页。

第一次了解项目，先读[项目介绍](./introduction.md)，判断它是否适合你的业务。决定试用后看[快速开始](./getting-started.md)；加入自己的工具、模型和知识库时，可以参考[开发指南](../tutorial/index.md)。不熟悉的术语见[术语表](./glossary.md)。

## 手册结构

| 分区 | 回答什么问题 |
| --- | --- |
| [项目介绍](./introduction.md) | 项目是什么、为什么使用、适用场景和同类项目选型 |
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

本手册覆盖 Apache-2.0 开源核心。现成的 IM / 电商消息渠道连接器不包含在本仓库中；外部业务系统可以通过 REST / SSE 或 `ChannelConnector` 扩展点自行接入，见[接入业务系统](./customization/business-integration.md)。
