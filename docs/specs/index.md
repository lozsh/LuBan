<!--
TEMPLATE-LOCK: 本文件章节锁定为以下清单，禁止新增/改名/调序：
1. 一句话定位
2. 模块导航
本文件只做导航，不承载正文知识；正文下沉到 modules/*.md、glossary.md、risks.md。
-->

# 规格总览 — LubanAgent

## 一句话定位

LubanAgent 是开源（Apache-2.0）的 Agent 工程基座：Python ≥3.11 + LangChain + LangGraph，提供 Agent 定义入库版本化、模型工厂、Runtime 治理、观测评测成本、图编排与多 Agent 协作的完整框架领地（`lubanagent/`），配合用户领地（`app/`）内置智能客服作为首个完整验证场景。

## 模块导航

| 模块 | 职责 | 文档 |
|---|---|---|
| core-extension-config | 领域模型契约（AgentDefinition/PromptDefinition/Run/Trace/BrainProfile/ToolContract 等）+ 共享脱敏组件（redact：秘密键规则/脱敏展示副本）+ 扩展点 Protocol/Registry/装饰器 + 环境化配置加载 | `modules/core-extension-config.md` |
| model | 模型工厂：OpenAI 兼容协议接入、BrainProfile→ModelDefinition 解析、能力校验与受控降级、FakeModel 零密钥兜底 | `modules/model.md` |
| runtime | Agent Runtime：LangGraph prebuilt agent 主循环 + 治理包装（步数/预算/超时/取消/SSE）、KV-cache 友好上下文组装 | `modules/runtime.md` |
| graph | LangGraph 手写图编排：Graph DSL、checkpoint 落库、human 人工节点、流程作 Tool 挂入 Agent | `modules/graph.md` |
| multiagent | 多 Agent 协作：supervisor 路由/子 Agent 委派 DSL、编排 Run 与预算继承/取消传播 | `modules/multiagent.md` |
| tool-mcp | Tool 执行治理（权限/人工确认/超时/重试/幂等）+ MCP Client/Server 与工具封装 | `modules/tool-mcp.md` |
| skill | Skill 能力包：渐进加载（元数据→正文→资源）、scripts/ 受控执行沙箱、SkillRegistry 扫描 | `modules/skill.md` |
| knowledge-memory | 最小 RAG 管线（多格式摄入/检索/Embedding）+ 防错答守门（相关度阈值/知识时效/全 miss 拒答短路）+ 工作台知识库管理（KnowledgeSourceManager + /v1/knowledge/* 托管源 CRUD/重切/检索测试）+ 会话/摘要/长期记忆策略，均为运行期可选能力 | `modules/knowledge-memory.md` |
| observability-trace-cost | Trace 写入（DB source of truth）+ 成本治理（估算/定价/归因/建议）+ 评测体系（断言含拒答硬指标/LLM-judge/回归/A-B 对比） | `modules/observability-trace-cost.md` |
| observability-adapters | 可插拔 ObservabilityBackend 适配器（Langfuse/LangSmith/noop）、失败隔离与降级、export_failures 留痕 | `modules/observability-adapters.md` |
| persistence | SQLAlchemy 模型 + 仓储层 + Alembic 迁移，23 张表（含 Prompt 库 prompts/prompt_versions、知识库 knowledge_sources、审批记录 approvals），是几乎所有其他模块的数据落地点 | `modules/persistence.md` |
| api-app-cli | REST/SSE 接入路由、鉴权委托、`create_app()` 装配根、`luban` 命令行 | `modules/api-app-cli.md` |
| app-user-land | 用户领地装配根 + 客服示例（Agent/Tool/Graph/Team/Skill/知识源）+ 部门项目问答范本（dept-project + project-docs）+ 独立 Mock 业务服务 | `modules/app-user-land.md` |
| tutorial | 新手动手教程（website/tutorial/，8+1 课）对外部事实的引用登记：命令语义/事件名/命名空间约定/互链，代码演进时的漂移抽查锚点 | `modules/tutorial.md` |

其他资产：`glossary.md`（跨模块术语）、`risks.md`（长期质量风险与复盘）。
