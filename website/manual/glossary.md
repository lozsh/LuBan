# 术语表

读手册撞上的术语都收在这里。每条一句话说清，再指到详解页面。

| 术语 | 一句话 | 详见 |
| --- | --- | --- |
| Agent 定义 | 一个 Agent 的全部要素（人设、指令、大脑、知识、记忆、能力、边界、契约），存库、版本化、运行期可改 | [Agent 定义](./features/agent-definition.md) |
| Agent 版本 | Agent 定义的不可变快照，一次 Run 绑定一个版本，改定义不影响在跑的和历史的 Run | [Agent 定义](./features/agent-definition.md) |
| Prompt 库 | 系统提示词的独立版本化管理：草拟、发版、多 Agent 复用；Agent 引用后解析全文进版本快照，Prompt 新版不自动生效 | [Prompt 库](./features/prompt-library.md) |
| 逻辑大脑 | fast / smart / cheap / vision 四个槽位，Agent 引用槽位而非具体模型名，换模型不改代码 | [模型工厂](./features/models.md) |
| 模型工厂 | 集中收敛各模型创建与参数差异的模块，新增厂商只动这里 | [模型工厂](./features/models.md) |
| Run | 一次 Agent 运行，绑定 Agent 版本与实际模型 | [运行观测](./features/observability.md) |
| Trace | 一次 Run 的完整现场记录：用了哪个版本/模型、调了几次模型、调了哪些 Tool、花了多少 Token/费用 | [运行观测](./features/observability.md) |
| 治理包装 | 套在 Agent 循环外层的代码：步数/预算/超时硬边界、能力匹配、SSE、Trace。是框架内部细节，用户不碰 | [运行观测](./features/observability.md) |
| 预算熔断 | Run 启动时给定 Token 预算，超限立即停，防止烧钱 | [运行观测](./features/observability.md) |
| KV-cache | 模型厂商的前缀缓存。上下文组装尽量保持稳定前缀以命中缓存，属尽力而为非保证 | [运行观测](./features/observability.md) |
| Tool | 业务动作，就是一段 `@tool` 标注的 Python 代码 | [Tool 与 MCP](./features/tools-mcp.md)、[写 Tool 与 Skill](./customization/tools-skills.md) |
| MCP | 行业标准工具协议。既能接别人的工具生态（Client），也能把自己的工具给别人（Server） | [Tool 与 MCP](./features/tools-mcp.md) |
| Skill | 行业标准的"知识与流程包"（SKILL.md），告诉 Agent 何时、怎么做某类事，渐进加载、入库版本化 | [Skill 能力包](./features/skills.md)、[写 Tool 与 Skill](./customization/tools-skills.md) |
| 图编排 | 必须按固定步骤走的业务流程（退款、审批）写成 LangGraph 流水线，代码级强制，不靠 Prompt 赌 | [图编排](./features/graphs.md)、[写图编排与团队](./customization/graphs-teams.md) |
| 检查点 / checkpoint | LangGraph 的运行状态持久化点，支撑流程暂停/恢复与人工审批节点；普通 Agent Run 也默认逐步落 checkpoint（`checkpoints` 表），崩溃后可调 resume API 续跑 | [图编排](./features/graphs.md)、[Agent Runtime](./features/runtime.md) |
| 多 Agent 协作 | supervisor 决定下一步交给哪个子 Agent，子 Agent 跑完回报，共享状态流转 | [多 Agent 协作](./features/multiagent.md)、[写图编排与团队](./customization/graphs-teams.md) |
| 子 Agent / 子 Run | 编排中被委派的 Agent 及其运行。子 Run 继承父 Run 预算、取消会传播 | [多 Agent 协作](./features/multiagent.md)、[写图编排与团队](./customization/graphs-teams.md) |
| supervisor | 多 Agent 编排里的调度角色，决定任务路由给哪个子 Agent | [多 Agent 协作](./features/multiagent.md) |
| SSE | Server-Sent Events，流式推送运行事件（token、tool 调用、完成）给调用方 | [SSE 事件与错误码](./api/sse-and-errors.md) |
| 开源核心 | Agent 本体 F1-F13，Apache-2.0，克隆即用 | [项目架构](./architecture.md) |
| 渠道层 | 商用模块 F14-F16（消息渠道接入、会话状态机、出站风控），开源版不含，可整体替换或卸载 | [项目架构](./architecture.md) |
| ChannelConnector | 框架留给渠道层的扩展点 Protocol，核心不依赖它也能独立运行 | [接入业务系统](./customization/business-integration.md) |
| 会话状态机 | AI 接待 ↔ 人工待接管 ↔ 人工接待 三态流转，属渠道层 | [项目架构](./architecture.md) |
| 出站风控 | 回复发出去前的审核（敏感词、承诺风险、平台规则），属渠道层 | [项目架构](./architecture.md) |
| 领地 | 仓库物理隔离：`lubanagent/` 框架领地（少改）与 `app/` 用户领地（你改业务的地方），合并上游无痛 | [改造总览](./customization/overview.md) |
| spec-coding | 本仓库内置的规格驱动开发 skill：spec→契约→编码→严格验证闭环，也是你开发自己 Agent 的方法论 | [用 spec-coding 开发你的 Agent](./customization/spec-coding.md) |
