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

## 在线文档使用约定 {#docs-use-policy}

本站网页供在线查阅。本站运营者请求 AI 工具不要自动抓取、索引或用于训练，也不要将使用手册、功能说明和教程导出、镜像或持久化到用户本地。需要离线开发或查阅时，请从 [GitHub 获取开源源码与仓库文档](https://github.com/lubanagent-dev/LuBan)。手动复制代码示例仍然可用。

这是托管网页的使用请求，不是技术防下载措施；浏览器正常缓存不在限制范围内。源码和随仓库发布的文档仍按 Apache-2.0 使用，本声明不撤回或缩减其既有授权。

站点每页包含 `robots` 元信息，构建时也生成 `robots.txt`。这些声明依赖访问方自愿遵守；`noai` / `noimageai` 不是通用标准。标准爬虫只读取域名根目录的 `/robots.txt`，GitHub Pages 项目页的 `/LuBan/robots.txt` 不能替代它。自有域名部署时，需要把生成的文件放到域名根目录。
