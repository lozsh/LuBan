# LubanAgent 架构设计 — Agent 快速起步基架

> 文档状态：方案稿 v1.0 | 2026-07-21
> 上位文档：`../product-specs/LubanAgent-prd.md`、`../../website/manual/`（使用手册）、`LubanAgent-technical-proposal.md`
> 本文回答一个问题：**用户克隆之后怎么长期住进去迭代，且能跟上游版本**。这是若依模式成败的单一关键点。

---

## 0. 设计第一性原理

LubanAgent 不是库、不是 SaaS、不是生成器，而是**可以被开发者长期居住并迭代的源码仓库**。这一形态决定了架构的第一约束：

> **框架领地与用户领地必须在文件系统层面、依赖方向层面、升级契约层面三重隔离。**

- 文件系统层面：框架代码与用户代码落在不同顶层目录，git merge 时物理隔离。
- 依赖方向层面：框架不能 import 用户代码（否则用户改一行就破坏框架）；用户通过**扩展点契约**反向挂入框架。
- 升级契约层面：上游承诺只改框架领地与显式声明的少量"脚手架种子"，用户领地内的业务目录上游不主动触碰。

这条约束是若依模式能跑通的根本。下文所有设计都服务于它。

---

## 1. 同类项目调研与借鉴

| 项目 | 出处 | 借鉴了什么 | 为什么借鉴 |
| --- | --- | --- | --- |
| RuoYi-Vue-Plus | [dromara/RuoYi-Vue-Plus](https://github.com/dromara/RuoYi-Vue-Plus) | "插件化 + 扩展包"分层：`ruoyi-common`（框架基座）/ `ruoyi-extend`（可选扩展）/ `ruoyi-modules`（业务模块）/ `ruoyi-admin`（装配根）。业务模块独立到 `modules/`，框架升级不动业务 | LubanAgent 与若依同形态：克隆即住、长期跟上游。直接借鉴"业务模块独立顶层目录 + 装配根最小化"的物理隔离手法 |
| RuoYi-Vue（原版） | [yangzongzhuan/RuoYi-Vue](https://github.com/yangzongzhuan/RuoYi-Vue) | Maven 多模块：`ruoyi-framework`（安全/JWT/拦截器）/ `ruoyi-system`（内置业务）/ `ruoyi-common`（工具）/ `ruoyi-admin`（启动）。新业务模块加在 `ruoyi-system` 旁边，依赖 framework/common | 借鉴"框架层稳定少动、业务层旁挂"的依赖方向：业务 → 框架，禁止反向 |
| Pydantic AI | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 三件套独立包：`pydantic_ai_slim`（核心）/ `pydantic_evals`（评测）/ `pydantic_graph`（图）；`Agent` 类 + `RunContext[Deps]` 类型化依赖注入；模型字符串引用 + provider 解析；声明式 agent spec | 借鉴"核心/评测/图"分包、RunContext 式 DI、模型 provider 模式。LubanAgent 在一个仓库内用 Python 子包对应，不拆多包，但职责切分一致 |
| vstorm-co/full-stack-ai-agent-template | [vstorm-co/full-stack-ai-agent-template](https://github.com/vstorm-co/full-stack-ai-agent-template) | 生成后无运行时锁定；后端分层 Routes → Services → Repositories；`agents/` 隔离框架特定代码、`rag/` 隔离检索管线；LLM provider 走 env 切换 | 借鉴"生成后即纯代码、无 generator 运行时依赖"理念，以及 agents/rag 物理隔离 |
| zhanymkanov/fastapi-best-practices | [zhanymkanov/fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices) | domain-based 布局：每个领域包内含 `router.py`/`schemas.py`/`models.py`/`service.py`/`dependencies.py`/`config.py`/`exceptions.py`；全局关注点放 `src/` 根，领域关注点放领域包内 | 借鉴领域包内聚布局，用于组织用户领地 `app/` 内的业务 Agent 模块 |
| Idun-Group/idun-agent-platform | [Idun-Group/idun-agent-platform](https://github.com/Idun-Group/idun-agent-platform) | `libs/` 三包：engine（FastAPI 工厂 + MCP registry + 可观测 + guardrails）/ standalone（CLI + admin REST + trace UI）/ schema（Pydantic 配置）；用户 agent 只给 `agent.py` + `config.yaml` + `.env`；多 provider 可观测适配器（Langfuse/Phoenix/LangSmith/GCP）；admin REST live reload | 借鉴可观测多 provider 适配器、配置驱动 + live reload、用户代码路径式加载 |
| agentscope-ai/agentscope-runtime | [agentscope-ai/agentscope-runtime](https://github.com/agentscope-ai/agentscope-runtime) | 框架作为可安装包 `src/agentscope_runtime/`，用户代码在 `examples/`；`AgentApp` 继承 FastAPI；sandbox + adapters 隔离工具执行 | 借鉴"框架包 vs 用户示例"的物理分层与 adapter 模式 |

**调研结论**：库型项目（Pydantic AI、AgentScope Runtime）靠"框架包 + 用户 app"天然隔离；脚手架型项目（若依）靠"顶层模块物理隔离 + 装配根最小化"保证可升级。LubanAgent 是脚手架型，采纳若依的物理隔离 + Pydantic AI 的扩展点契约 + Idun 的多 provider 适配器。

---

## 2. 顶层目录结构（树状，文件级）

```text
lubanagent-repo/
├── lubanagent/                       # 【框架领地】上游追踪，用户原则上不动
│   ├── __init__.py                   # 仅 __version__；公开 API 从子模块导入（见下注）
│   ├── core/                         # 稳定领域模型 + 契约（最底层，无外部依赖）
│   │   ├── definition.py             #   AgentDefinition / AgentVersion / IOContract
│   │   ├── prompt.py                 #   PromptDefinition / PromptVersion（Prompt 库一等实体）
│   │   ├── run.py                    #   Run / RunStatus / RunContext[Deps]
│   │   ├── trace.py                  #   Trace / TraceEvent / Step
│   │   ├── brain.py                  #   BrainProfile (fast/smart/cheap/vision)
│   │   ├── model.py                  #   ModelDefinition / ModelCapability
│   │   ├── tool.py                   #   ToolDescriptor / RiskLevel / ToolContract
│   │   ├── budget.py                 #   Budget / TokenEstimate
│   │   ├── skill.py                  #   SkillMeta / SkillPackage
│   │   └── errors.py                 #   LubanError 体系（公共错误码）
│   ├── extension/                    # 扩展点契约（用户实现这些 Protocol 来挂入框架）
│   │   ├── registry.py               #   Registry: tool/graph/skill/auth/observability/hook
│   │   ├── protocols.py              #   ToolProvider / GraphProvider / AuthProvider /
│   │   │                             #   ObservabilityBackend / Hook / KnowledgeConnector /
│   │   │                             #   ChannelConnector / SessionStateStore / OutputGuard（渠道层扩展点，开源核心定义、商用渠道层实现）
│   │   └── decorators.py             #   @tool / @graph / @hook 装饰器
│   ├── model/                        # 模型工厂（F2）
│   │   ├── factory.py                #   ModelFactory 注册表 + 解析
│   │   ├── openai_compat.py          #   OpenAI 兼容工厂（DeepSeek/Qwen/GLM/vLLM/Ollama）
│   │   ├── brain_resolver.py         #   BrainProfile → ModelDefinition 解析
│   │   ├── capability.py             #   能力声明 + 启动前校验 + 受控降级
│   │   └── fakes.py                  #   FakeModel（零密钥兜底，测试/演示）
│   ├── runtime/                      # Agent Runtime（F3）
│   │   ├── agent_loop.py             #   LangGraph prebuilt agent + 治理包装（步数/预算/超时/取消/SSE）
│   │   ├── context_builder.py        #   KV-cache 友好上下文组装（F7 稳定前缀 + append-only）
│   │   ├── budget_guard.py           #   运行预算 + 超限熔断
│   │   ├── sse.py                    #   统一 SSE 事件协议
│   │   ├── cancellation.py           #   取消/超时
│   │   └── capabilities.py           #   运行期能力件（预算/取消/SSE 装配）
│   ├── tool/                         # Tool 治理（F4）
│   │   ├── executor.py               #   权限检查 → 人工确认 → 超时/重试/幂等 → 执行
│   │   ├── approval.py               #   高风险人工确认（异步暂停/恢复）
│   │   └── mock.py                   #   Tool 独立调试与 Mock
│   ├── mcp/                          # MCP（F4）
│   │   ├── client.py                 #   MCP Client（接外部 Server）
│   │   ├── server.py                 #   自有 Tool 反向暴露为 MCP Server
│   │   └── registry.py               #   MCP Server 登记 + 工具封装
│   ├── skill/                        # Skill 运行时（F12）
│   │   ├── loader.py                 #   渐进加载（元数据 → 正文 → 资源）
│   │   ├── sandbox.py                #   scripts/ 受控执行
│   │   └── registry.py               #   SkillRegistry（app/skills/ 扫描）
│   ├── knowledge/                    # 最小 RAG（F9）
│   │   ├── connector.py              #   KnowledgeConnector Protocol
│   │   ├── ingest.py                 #   解析/切块/向量化
│   │   ├── retrieve.py               #   检索 + 引用
│   │   ├── embedding.py              #   Embedding 抽象 + FakeEmbedding（零密钥兜底）
│   │   └── store.py                  #   KnowledgeStore（向量落库）
│   ├── memory/                       # 记忆策略（F9）
│   │   ├── session.py                #   会话记忆
│   │   ├── summary.py                #   摘要记忆
│   │   └── long_term.py              #   长期记忆
│   ├── graph/                        # LangGraph 编排（F11）
│   │   ├── builder.py                #   Graph DSL（.step().human().when()）
│   │   ├── checkpoint.py             #   checkpointer 落自有 MySQL
│   │   ├── tool_adapter.py           #   流程作为 Tool 挂入 Agent
│   │   └── runner.py                 #   图执行器（预算/取消继承、与 Runtime 同治理）
│   ├── multiagent/                   # 多 Agent 协作（F13）
│   │   ├── supervisor.py             #   supervisor 路由 / 子 Agent 委派
│   │   ├── team.py                   #   MultiAgent DSL（.supervisor().agent().route()）
│   │   └── state.py                  #   共享状态 / 消息传递
│   ├── observability/                # 治理层（F5/F6/F7）
│   │   ├── trace/                    #   Trace 写入（DB source of truth）
│   │   ├── evals/                    #   测试集/断言/LLM-judge/回归/录制重放（F6）
│   │   ├── cost/                     #   Token 估算/费用归因/报表（F7）
│   │   └── adapters/                 #   外发适配器（见 §6）
│   │       ├── base.py               #     ObservabilityBackend Protocol
│   │       ├── noop.py
│   │       ├── langfuse.py
│   │       ├── langsmith.py
│   │       └── registry.py           #     适配器注册表 + SafeBackend 包装
│   ├── persistence/                  # DB 仓储（迁移在仓库根 migrations/，配置在根 alembic.ini）
│   │   ├── models/                   #   SQLAlchemy 模型（agents/agent_versions/prompts/prompt_versions/runs/traces/...）
│   │   └── repositories/             #   仓储层（含 resolve_prompt_reference：Agent 引用 Prompt 落库前解析）
│   ├── api/                          # REST/SSE 接入骨架（F8）
│   │   ├── routes/                   #   /v1/agents/{key}/runs、/v1/prompts 等
│   │   ├── auth.py                   #   API Key 鉴权（委托给 AuthProvider）
│   │   ├── deps.py                   #   FastAPI 依赖注入
│   │   ├── errors.py                 #   HTTP 错误映射
│   │   └── schemas.py                #   请求/响应 Pydantic 模型
│   ├── config/                       # 配置加载
│   │   ├── settings.py               #   pydantic-settings（env 分层覆盖）
│   │   ├── defaults.py               #   缺省常量（USER_EXTENSION_PACKAGES 等）
│   │   └── logging.py                #   configure_logging
│   ├── app/                          # FastAPI app factory + lifespan
│   │   ├── factory.py                #   create_app()：装配路由、扩展点、observability
│   │   └── lifespan.py               #   启动/关闭钩子
│   └── cli/                          # luban 命令行（init/eval/migrate/export）
│       ├── main.py
│       └── yaml_io.py                #   Agent YAML 导入/导出 IO
│   ├── ui/                           # 开发者工作台前端工程（React 18 + Vite + TS + Tailwind + shadcn/ui，见 §7.10）
│   │   ├── src/
│   │   ├── package.json
│   │   └── vite.config.ts            #   npm run dev 独立起；build 后由 FastAPI 托管静态资源
│
├── app/                              # 【用户领地】用户的地盘，随便改
│   ├── main.py                       # 装配根：调用框架 create_app()，手动注册 Skill/Knowledge/Auth（关键文件，见 §4）
│   ├── agents/                       # 业务 Agent 定义（YAML 初始化/导出；运行期存 DB）
│   │   ├── customer_service.yaml     #   客服主 Agent（上游种子，可改可复制）
│   │   ├── cs_faq.yaml               #   FAQ 子 Agent（上游种子）
│   │   ├── cs_refund.yaml            #   退款子 Agent（上游种子）
│   │   ├── cs_triage.yaml            #   分流子 Agent（上游种子）
│   │   └── <my_agent>.yaml           #   用户新增 Agent = 新文件
│   ├── tools/                        # 业务 Tool（Python 代码）
│   │   ├── customer_service/         #   客服示例 Tool（上游种子）
│   │   │   ├── order.py
│   │   │   └── refund.py
│   │   └── <my_module>/              #   用户新增 Tool = 新目录
│   ├── graphs/                       # 业务确定性流程（F11）
│   │   └── refund_flow.py            #   客服示例退款流水线
│   ├── multiagents/                  # 多 Agent 协作拓扑（F13）
│   │   └── cs_team.py                #   客服示例：分流 → FAQ / 退款
│   ├── skills/                       # Skill 包目录（F12）
│   │   └── refund-sop/SKILL.md
│   ├── knowledge_sources/            # 知识库连接器实现（F9）
│   │   └── product_faq.py
│   ├── datasets/                     # 评测测试集（F6）
│   │   └── cs_basic.yaml
│   ├── api/                          # 业务自定义路由（挂到 FastAPI）
│   │   └── teams.py                  #   客服团队路由（上游种子）
│   └── config/                       # env / settings 覆盖
│       ├── .env.example
│       └── settings.local.py
│   # 注：app/prompts/ 与 app/extensions/ 为可选目录，用户按需创建（非种子）
│
├── lubanagent-channels/              # 【商用·独立可拆卸模块】消息渠道层（F14/F15/F16），不进开源仓库
│   ├── connectors/                   #   ChannelConnector 实现：微信/飞书/钉钉 + 电商客服（京东/淘宝/拼多多）
│   ├── session/                      #   会话状态机（AI接待 ↔ 人工待接管 ↔ 人工接待）
│   ├── guard/                        #   出站风控（敏感词/承诺风险/平台规则审核）
│   └── reliability/                  #   幂等去重 / 游标补偿 / 长连接重连 / 限流削峰 / 授权与接口变更巡检
│   # ↑ 单向依赖 lubanagent/（调用 Agent Runtime）；反向禁止，import-linter 守卫
│
├── demos/                            # 旗舰示例（依赖 lubanagent + app，不反向依赖）
│   └── customer_service/             #   客服 Mock 业务服务（订单/退款/产品库）+ 数据 + 文档
│       ├── mock_services/
│       ├── data/
│       └── README.md
│
├── tests/                            # 测试（框架测试 + 示例回归）
│   ├── lubanagent/                   #   框架单元/集成测试（随上游更新）
│   └── demos/                        #   示例回归测试（随上游更新）
│
├── migrations/                       # DB schema 迁移入口（Alembic versions + env.py）
├── alembic.ini                       # Alembic 配置（指向 migrations/）
├── docker/                           # Dockerfile + compose
├── scripts/                          # 运维脚本
├── docs/                             # 文档（随上游更新）
├── .lubanagent-upstream              # 上游追踪清单（见 §4.4）
├── pyproject.toml                    # 依赖锁定（上游维护）
├── docker-compose.yml
├── CHANGELOG.md                      # 必须按领地分类（见 §4.4）
├── UPGRADE.md                        # 版本迁移指南
├── LICENSE                           # Apache-2.0
└── README.md
```

**顶层领地**（开源仓库含前三类；渠道层为商用独立模块，不进开源仓库）：
- `lubanagent/` —— 框架领地（上游追踪）
  - 注：`lubanagent/__init__.py` 只放 `__version__`，不 re-export 符号。公开 API 按子模块导入：`from lubanagent.extension import tool, graph, hook, default_registry`、`from lubanagent.app.factory import create_app`、`from lubanagent.core import AgentDefinition`。
- `app/` —— 用户领地（用户做主）
- `demos/` `tests/` `docs/` `docker/` `scripts/` `migrations/` —— 共享领地（上游为主，用户少改）
- `lubanagent-channels/` —— **商用渠道层**（独立可拆卸模块，开源版不含；单向依赖 `lubanagent/`，反向禁止）。开源核心只在其 `extension/protocols.py` 定义 `ChannelConnector`/`SessionStateStore`/`OutputGuard` 三个扩展点，渠道层负责实现；不装渠道层时核心通过 REST/SSE 独立运行。

---

## 3. 模块职责与依赖方向

### 3.1 分层依赖图

```mermaid
flowchart TB
    subgraph L5["用户领地 app/（用户做主）"]
        MAIN["app/main.py（装配根）<br/>app/tools/* · app/graphs/* · app/skills/*<br/>app/knowledge_sources/* · app/api/*"]
    end

    subgraph CH["渠道层（商用 · 可拆卸 · 开源版不含）"]
        CHANNELS["lubanagent-channels/*<br/>connector · 会话状态机 · 出站风控"]
    end

    subgraph L4["接入与装配（框架领地）"]
        API["lubanagent/api（REST/SSE 接入）<br/>lubanagent/app（create_app 装配）"]
    end

    subgraph L3["执行层（LangGraph 一种引擎 · 两种用法）"]
        RT["lubanagent/runtime<br/>普通 Agent：prebuilt agent + 治理包装"]
        GR["lubanagent/graph<br/>确定性流程 · checkpoint · 编排"]
    end

    subgraph L2["能力层与治理层"]
        CAP["lubanagent/model · tool · mcp · skill<br/>knowledge · memory · observability"]
    end

    subgraph L1["底层"]
        BASE["lubanagent/core · extension · config · persistence"]
    end

    MAIN -->|"注册扩展点（运行期，经 Registry）"| API
    CHANNELS -->|"单向调用 Runtime（反向禁止）"| RT
    API --> RT
    API --> GR
    RT --> CAP
    GR --> CAP
    CAP --> BASE
```

> 图中画渠道层是为了交代它不在框架四层之内：它是核心之外的独立调用方（消费者不是扩展），参与装配与 Registry 注册的只有用户领地；依赖方向细则见 §3.2 规则 6。

### 3.2 依赖方向规则（强制）

1. **`app/*` → `lubanagent/*`**：允许。用户代码 import 框架公开 API。
2. **`lubanagent/*` → `app/*`**：**禁止**（import 期）。框架只能通过 `lubanagent.extension.registry` 在运行期拿到用户注册的对象，registry 的接口契约定义在 `lubanagent.extension.protocols`。
3. **`lubanagent` 子包之间**：按上图分层，下层不依赖上层。`core` 是最底层，不依赖任何兄弟包；`runtime`/`graph` 依赖 `core`/`model`/`tool`/`observability`，不能反向。
4. **`demos/*` → `lubanagent/*` + `app/*`**：允许。`app/*` 与 `lubanagent/*` 都不能反向依赖 `demos/*`（技术提案 §15 已列为风险，这里落地为 lint 规则）。
5. **CI 守卫**：用 `import-linter` 在 CI 里固化上述契约，违反即构建失败。
6. **渠道层（商用·可拆卸）**：`lubanagent-channels/*` → `lubanagent/*` 允许（渠道层调用核心 Runtime 拿回复）；**`lubanagent/*` → `lubanagent-channels/*` 禁止**。渠道层是独立商用模块、不进开源仓库；核心只通过 `lubanagent.extension.protocols` 的 `ChannelConnector`/`SessionStateStore`/`OutputGuard` 三个扩展点与之解耦，实现可整体替换或卸载。不装渠道层时，业务系统经 `lubanagent/api`（REST/SSE）直接调用核心，核心独立可运行（被动模式）；装上后，外部消息经渠道层 → 会话状态机 → Runtime → 出站风控 → 回复（主动模式）。import-linter 同步守卫此方向。

> 上图未画渠道层：它是核心之外的独立调用方（商用、可拆卸），单向调用 `lubanagent/runtime`，不参与核心装配与 Registry 注册（它是消费者不是扩展）。

### 3.3 各模块职责

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `lubanagent.core` | 领域模型与契约（dataclass/Pydantic + Protocol） | 不含业务逻辑、不依赖 DB/HTTP |
| `lubanagent.extension` | 扩展点 Protocol + Registry + 装饰器 | 不实现具体扩展 |
| `lubanagent.model` | 模型工厂、Brain 解析、能力校验、受控降级 | 不绑定具体厂商 SDK 到业务层 |
| `lubanagent.runtime` | 普通 Agent：LangGraph prebuilt agent + 治理包装（预算熔断/SSE/取消） | 不做手写图编排 |
| `lubanagent.graph` | LangGraph 流水线、checkpoint、人工节点、流程作 Tool | 不做普通对话循环 |
| `lubanagent.tool` | Tool 执行治理（权限/确认/超时/重试/幂等/Mock） | 不实现具体业务 Tool |
| `lubanagent.mcp` | MCP Client/Server、工具封装 | 不持有业务 Tool 实现 |
| `lubanagent.skill` | Skill 渐进加载与受控执行 | 不做 Skill 市场 |
| `lubanagent.knowledge` | 最小 RAG 管线 + Connector 协议 + 防错答守门（阈值/时效/全 miss 拒答短路，`KnowledgeConfig` 三字段，短路执行点在 runtime `agent_loop`） | 不做复杂重排/多库联邦 |
| `lubanagent.memory` | 会话/摘要/长期记忆策略 | 不强制启用 |
| `lubanagent.observability` | Trace 写库 + 评测 + 成本 + 外发适配器 | 不依赖外部 SaaS 才能跑 |
| `lubanagent.persistence` | SQLAlchemy 模型 + 仓储 + Alembic | 不含业务规则 |
| `lubanagent.api` | REST/SSE 路由 + 鉴权委托 + 错误映射 | 不含业务逻辑 |
| `lubanagent.config` | 环境化配置 | 不读用户业务配置 |
| `lubanagent.app` | `create_app()` 装配 + lifespan | 不注册业务路由 |
| `lubanagent.cli` | `luban` 命令行 | 不含业务命令 |
| `app.main` | 装配根：调用 `create_app()`，注册扩展点 | 不实现框架能力 |
| `app.tools/*` | 业务 Tool Python 代码 | 不改框架 |
| `app.agents/*.yaml` | Agent 定义初始化/导出 | 不是运行期主存储 |
| `app.graphs/*` | 业务确定性流程 | 按需启用 |
| `app.main` 注册项 | 在 `app/main.py` 内注册 AuthProvider/ObservabilityBackend/Hook/SkillRegistry/KnowledgeSource | 不实现框架默认项 |
| `app.api/*` | 业务自定义路由 | 不挂框架内部路由 |
| `lubanagent-channels/*` | **商用渠道层**：ChannelConnector 实现 + 会话状态机 + 出站风控 + 渠道可靠性 | 不进开源仓库；单向依赖 `lubanagent/`、反向禁止；不装也能跑 |

---

## 4. 用户怎么改才不破坏升级（核心章节）

下图是本章的全景：用户代码只落在用户领地，框架运行期经 `Registry` 拿用户扩展，上游升级永不触碰用户领地。

```mermaid
flowchart LR
    subgraph USER["用户领地 app/（用户做主）"]
        CODE["@tool / @graph / @hook 装饰的业务代码<br/>app/tools · app/graphs · app/skills · app/datasets"]
        MAIN["app/main.py 装配根<br/>手挂 AuthProvider · ObservabilityBackend · 知识源 · Skill"]
    end

    REG["Registry<br/>lubanagent.extension.registry"]

    subgraph FW["框架领地 lubanagent/（上游追踪，用户原则上不动）"]
        DISC["discover_packages(settings.extension_packages)<br/>容错扫描装饰器自动注册"]
        CORE["api · runtime · graph · model · tool · observability<br/>运行期只从 Registry 取实例，绝不 import 用户模块"]
    end

    UP["上游新版本<br/>.lubanagent-upstream 清单 + CI 守卫<br/>永不触碰用户领地"]

    CODE -->|"被扫描发现"| DISC
    MAIN -->|"register_* 手挂"| REG
    DISC --> REG
    REG --> CORE
    UP -.->|"merge 只落在框架领地"| FW
```

一句话判据：**用户领地里“加文件”永远安全；“改框架文件”永远危险**。下面的改动类型表、扩展点契约和升级策略都是这句话的展开。

### 4.1 三类改动与升级安全性

| 改动类型 | 落在哪里 | 升级影响 |
| --- | --- | --- |
| **新增业务 Agent** | `app/agents/<name>.yaml` + DB 记录 | 零冲突 |
| **新增业务 Tool** | `app/tools/<module>/<name>.py`，用 `@tool` 装饰 | 零冲突 |
| **新增确定性流程** | `app/graphs/<name>.py`，用 `@graph` 装饰 | 零冲突 |
| **新增 Skill** | `app/skills/<name>/SKILL.md` | 零冲突 |
| **新增知识源** | `app/knowledge_sources/<name>.py` 实现 `KnowledgeConnector` | 零冲突 |
| **新增评测集** | `app/datasets/<name>.yaml` | 零冲突 |
| **自定义鉴权** | 在 `app/main.py` 实现 `AuthProvider` 并 `default_registry.register_auth(...)` | 零冲突 |
| **自定义观测后端** | 在 `app/main.py` 实现 `ObservabilityBackend` 并 `register_observability(...)` | 零冲突 |
| **业务钩子** | 用 `@hook` 装饰，或在 `app/main.py` `register_hook(...)` | 零冲突 |
| **业务自定义路由** | `app/api/<name>.py`，在 `main.py` 挂载 | 零冲突 |
| **改客服示例** | 复制 `app/agents/customer_service.yaml` → 新文件再改；不原地改 | 零冲突 |
| **改框架默认行为** | **不要做**。用扩展点/Hook 实现；确实要改则 fork 框架并提 PR | 高冲突，丢升级 |
| **改 `app/main.py` 装配根** | 偶尔必要 | 低冲突，上游标注 |
| **改 `pyproject.toml` 依赖** | 用户加业务依赖在末尾 | 低冲突，上游标注 |

**核心规则**：**新增 = 零冲突；改框架 = 丢升级**。用户领地里"加文件"永远安全；"改框架文件"永远危险。

### 4.2 扩展点契约（框架与用户的接口面）

所有用户挂入框架的代码，必须通过 `lubanagent.extension.protocols` 里定义的 Protocol。扩展注册机制是**约定优于配置的扫描 + 装配根可选手挂**，不依赖 `app/extensions/` 目录：

1. `app/main.py`（用户装配根）调用框架 `create_app(settings=settings, registry=default_registry, db=db)`；
2. 框架 `lubanagent/app/factory.py` 启动时对 `settings.extension_packages`（默认 `("app.tools","app.graphs")`，见 `lubanagent/config/defaults.py`）逐个调 `lubanagent.extension.discover([package], registry=registry)`，动态导入子模块、扫描 `@tool`/`@graph`/`@hook` 装饰器，**自动**完成注册；
3. 需要注册 Skill / 知识源 / 自定义 AuthProvider / ObservabilityBackend 时，用户在 `app/main.py` 内手动操作 `default_registry`（或自建 `Registry` 传入），无独立 `app/extensions/registry.py`；
4. 框架运行期只从 `Registry` 取实例，绝不 import 用户模块名。

扩展点清单：

```python
# lubanagent/extension/protocols.py（节选，示意非实现）
class ToolProvider(Protocol):
    def tools(self) -> list[ToolDescriptor]: ...

class GraphProvider(Protocol):
    def graphs(self) -> list[GraphDescriptor]: ...

class AuthProvider(Protocol):
    async def verify(self, request: AuthRequest) -> Principal: ...
# AuthRequest 是框架中立模型（headers/query_params），api/auth.py 负责从 FastAPI Request 适配到 AuthRequest

class ObservabilityBackend(Protocol):
    name: str
    def start_trace(self, run: Run) -> TraceHandle: ...
    def emit(self, handle: TraceHandle, event: TraceEvent) -> None: ...
    def end_trace(self, handle: TraceHandle, status: RunStatus, summary: dict | None = None) -> None: ...

class Hook(Protocol):  # 全 async
    async def pre_run(self, ctx: RunContext) -> None: ...
    async def pre_tool(self, ctx: RunContext, tool: ToolDescriptor, args: dict) -> None: ...
    async def post_step(self, ctx: RunContext, step: Step) -> None: ...

class KnowledgeConnector(Protocol):
    async def retrieve(self, query: str, top_k: int) -> list[Snippet]: ...
```

Registry 方法是单数形式（`lubanagent/extension/registry.py`）：

```python
# 方法签名（节选）
default_registry.register_tool(descriptor, handler)   # 单数；@tool 装饰器已自动调
default_registry.register_graph(descriptor)          # @graph 装饰器已自动调
default_registry.register_hook(hook)                  # 单数；@hook 装饰器已自动调
default_registry.register_auth(provider)             # 不注册则用框架默认 API Key
default_registry.register_knowledge(name, connector) # 知识源
default_registry.register_observability(backend)     # 覆盖 env 选择
default_registry.register_observability_backend(name, backend)  # 命名后端选址
```

`@tool` / `@graph` / `@hook` 装饰器（`lubanagent/extension/decorators.py`）在模块被 `discover` 导入时即自动注册，用户**绝大多数场景无需手写**任何 `register_*`。只有 Skill 包、知识源灌入、自定义鉴权 / 观测后端等少数场景，在 `app/main.py` 内手动注册：

```python
# app/main.py（示意，真实种子见仓库）
from lubanagent.app.factory import create_app as _framework_create_app
from lubanagent.extension import default_registry
from lubanagent.skill import SkillRegistry
from lubanagent.knowledge import FakeEmbedding, KnowledgeStore

def create_app():
    settings = get_settings()
    db = create_database(settings)
    # 知识源、SkillRegistry 等手动挂入
    skill_registry = SkillRegistry(skills_root=Path(settings.skills_root))
    store = KnowledgeStore(db)
    make_product_faq_connector(store, FakeEmbedding())

    async def _ingest():
        await skill_registry.scan()
        await ingest_product_faq(store, FakeEmbedding())

    app = _framework_create_app(
        settings=settings, registry=default_registry, db=db,
        on_startup=_ingest,
    )
    # observability 通过 env OBSERVABILITY_BACKEND=langfuse 选择，亦可在此 register_observability 覆盖
    return app
```

### 4.3 用户开发路径：从克隆到加一个业务 Agent

以"加一个 HR 政策助手"为例，**全程不改框架领地**：

1. 在 `app/agents/hr_assistant.yaml` 写定义（身份/指令/大脑/工具/边界）。
2. 在 `app/tools/hr/` 下写 Python Tool：`query_policy.py`、`submit_leave.py`，每个函数用 `@tool(description=..., risk=..., timeout=...)` 装饰。
3. （可选）`app/skills/leave-sop/SKILL.md` 写请假 SOP。
4. （可选）`app/datasets/hr_basic.yaml` 写评测集。
5. 启动服务，框架自动发现 `app/tools/hr/` 下的 `@tool`、`app/agents/hr_assistant.yaml` 通过 `luban init` 导入 DB。
6. 通过 `POST /v1/agents/hr_assistant/runs` 调用，或在工作台调试。

**代码落在 `app/` 下的新目录，与上游零交集。**

### 4.4 升级策略落地

**`.lubanagent-upstream` 清单文件**（仓库根，上游维护）：

```ini
# 上游追踪目录清单——这些目录/文件上游会主动变更
# 完整清单见仓库根 .lubanagent-upstream；此处为结构示意（节选）
[framework]
lubanagent/

[shared]
# 共享领地，上下游都会演进（节选，完整含 .github/alembic.ini/importlinter.toml/
# openapi.snapshot.json/README.md/AGENTS.md/CLAUDE.md/CHANGELOG.md/UPGRADE.md 等共 17 项）
demos/
tests/
docs/
docker/
scripts/
migrations/
pyproject.toml
docker-compose.yml

[scaffold-seed]
# 脚手架种子：首次克隆提供可跑内容，之后上游仅做破坏性变更时才动
app/__init__.py
app/**/__init__.py
app/main.py
app/config/.env.example
app/agents/customer_service.yaml
app/agents/cs_faq.yaml
app/agents/cs_refund.yaml
app/agents/cs_triage.yaml
app/tools/customer_service/
app/graphs/refund_flow.py
app/multiagents/cs_team.py
app/skills/refund-sop/
app/knowledge_sources/product_faq.py
app/datasets/cs_basic.yaml
app/api/teams.py

[user-owned]
# 用户领地：上游不主动触碰（除 scaffold-seed 列出的初始文件）
app/agents/!(customer_service|cs_faq|cs_refund|cs_triage).yaml
app/tools/!(customer_service)/
app/graphs/!(refund_flow).py
app/multiagents/!(cs_team).py
app/skills/!(refund-sop)/
app/knowledge_sources/!(product_faq).py
app/datasets/!(cs_basic).yaml
app/api/!(teams).py
app/extensions/         # 整目录占位：用户可选创建（放自定义 auth/observability/hooks 等）
app/config/settings.local.py
```

**CHANGELOG 必须按领地分类**，每次发布带标签：

- `[framework]` —— 框架领地变更，用户合并 `lubanagent/` 即可。
- `[scaffold-seed]` —— 脚手架种子变更，CHANGELOG 必须给出"保留用户改动"的合并指令（如 `git merge -X ours`）。
- `[shared]` —— 共享目录变更，按需合并。
- `[migration]` —— DB schema 变更，必须给出 `alembic upgrade` 命令与回滚步骤。
- `[breaking]` —— 扩展点 Protocol 或 `core` 契约的不兼容变更，必须升 major 版本并在 UPGRADE.md 给迁移示例。

**升级工作流（用户侧）**：

```bash
git remote add upstream https://github.com/lubanagent/lubanagent.git
git fetch upstream
git merge upstream/master
# 冲突预期只在 scaffold-seed 文件；按 UPGRADE.md 指令处理
alembic upgrade head   # 如有 [migration]
pip install -e .       # 如有 [framework] 依赖变更
```

**关键不变量**：用户领地（`app/tools/<my>/`、`app/agents/<my>.yaml` 等）**永远不出现在上游 commit** 的 diff 里，因为上游维护者通过 `.lubanagent-upstream` 的 `user-owned` glob 自我约束，CI 也校验上游 PR 不触碰用户领地。

---

## 5. F1-F16 功能落到模块映射

> F1-F13 属开源核心（框架/用户领地）；F14-F16 属商用渠道层（独立模块，开源版不含）。

| 功能 | 启用方式 / 归属 | 框架领地（`lubanagent/`） | 用户领地（`app/`） | 备注 |
| --- | --- | --- | --- | --- |
| F1 Agent 定义与版本化 | 默认 | `core/definition.py`、`core/prompt.py`（Prompt 库一等实体）、`persistence/models/agent*`、`persistence/models/prompt*`、`persistence/repositories/agent.py`、`persistence/repositories/prompt.py`（含 `resolve_prompt_reference`）、`api/routes/prompts.py`、`cli/`（import/export） | `app/agents/*.yaml`（初始化/导出，纯内嵌指令语法不变） | 运行期主存储是 DB，YAML 仅 init/export；Agent 定义可经 `prompt_key`/`prompt_version` 引用 Prompt 库，解析在落库前（内容写入 instructions 落进版本快照），运行期只读快照、不回查 `prompts` 表 |
| F2 模型中心与工厂 | 默认 | `model/factory.py`、`model/openai_compat.py`、`model/brain_resolver.py`、`model/capability.py`、`persistence/models/model*` | 无（厂商适配全在框架） | 新增厂商 = 在 `lubanagent/model/` 加工厂代码（框架领地，PR 上游） |
| F3 Agent Runtime | 默认 | `runtime/agent_loop.py`、`runtime/context_builder.py`、`runtime/budget_guard.py`、`runtime/sse.py` | 无 | 普通 Agent = LangGraph prebuilt agent + 治理包装，不重写循环 |
| F4 Tool/MCP 治理 | 默认 | `tool/executor.py`、`tool/approval.py`、`tool/mock.py`、`mcp/client.py`、`mcp/server.py` | `app/tools/*`（业务 Tool，`@tool` 自动注册）、`app/main.py`（MCP 手动登记） | 业务 Tool 是 Python 代码，落用户领地 |
| F5 运行观测 | 默认 | `observability/trace/`、`observability/adapters/`、`persistence/models/trace.py`（含 `export_failures` 表） | `app/main.py`（可选 `register_observability_backend` 自定义后端） | Trace 内部 DB 为 source of truth；外发后端可插拔 |
| F6 评测体系 | 默认 | `observability/evals/`（测试集加载、断言、LLM-judge、回归、录制重放、A/B 对比）、`cli/`（`luban eval`） | `app/datasets/*.yaml` | 评测数据是用户资产 |
| F7 成本治理 | 默认 | `observability/cost/`、`runtime/budget_guard.py`、`runtime/context_builder.py`（KV-cache 友好不变量） | 无 | 熔断挂在 Runtime |
| F8 工程基座 | 默认 | `api/routes/`、`api/auth.py`、`api/deps.py`、`config/settings.py`、`persistence/`、`docker/`、`app/factory.py` | `app/main.py`（可选 `register_auth`）、`app/config/.env` | 鉴权可插拔 |
| F9 知识与记忆 | 可选 | `knowledge/`、`memory/` | `app/knowledge_sources/*` | 可选启用，简单 Agent 不引入 |
| F10 开发者工作台 | 可选 | `lubanagent/ui/`（前端工程，独立子目录，用户少改） | 无 | 工作台是开发辅助，不做托管 |
| F11 图编排 | 可选 | `graph/builder.py`、`graph/checkpoint.py`、`graph/tool_adapter.py` | `app/graphs/*.py` | 流程作 Tool 挂入 Agent |
| F12 Skill 能力包 | 可选 | `skill/loader.py`、`skill/sandbox.py`、`skill/registry.py`、`persistence/models/misc.py`（`SkillRow` 即 skills 表） | `app/skills/*/SKILL.md` | 兼容 Agent Skills 开放标准 |
| F13 多 Agent 协作 | 可选·核心 | `multiagent/supervisor.py`、`multiagent/team.py`、`multiagent/state.py` | `app/multiagents/*.py` | 编排 Run 与单 Agent Run 同治理；依赖 F11 |
| F14 消息渠道接入层 | 渠道层·商用 | `extension/protocols.py`（`ChannelConnector` 定义） | — | 商用模块 `lubanagent-channels/connectors/` 实现；含统一消息模型与渠道可靠性 |
| F15 会话状态机 | 渠道层·商用 | `extension/protocols.py`（`SessionStateStore` 定义） | — | 商用模块 `lubanagent-channels/session/`；AI 接待↔人工待接管↔人工接待 |
| F16 出站风控 | 渠道层·商用 | `extension/protocols.py`（`OutputGuard` 定义） | — | 商用模块 `lubanagent-channels/guard/`；回复发出前审核 |

---

## 6. 观测适配器（Langfuse/LangSmith 可插拔）

### 6.1 双写模型

观测数据有**两个去向**，职责分离：

1. **内部 DB（always on）**：`runs` / `traces` / `trace_events` / `token_usages` / `cost_records` 表，由 `lubanagent.observability.trace.TraceWriter` 同步写入。这是**评测（F6）、成本归因（F7）、工作台 Trace 查看（F10）的唯一数据源**，不依赖任何外部 SaaS，保证"数据不出企业边界"。
2. **外部后端（optional，可插拔）**：通过 `ObservabilityBackend` 适配器把 Trace 事件流外发到 Langfuse / LangSmith 等，用于复用其 UI、告警、看板。**关掉也不影响任何核心功能**。

```mermaid
flowchart LR
    RUN["每次 Run<br/>（Runtime / 图 / 多 Agent 统一产出事件）"] --> TW["TraceWriter<br/>同步写库"]
    TW --> DB[("内部 DB（source of truth）<br/>runs · traces · trace_events<br/>token_usages · cost_records")]
    DB -->|"唯一数据源"| USE["评测回归 F6 · 成本归因 F7<br/>工作台 Trace 查看 F10"]
    RUN --> SB["SafeBackend 包装<br/>ObservabilityBackend Protocol"]
    SB --> LF["langfuse（默认 · 自部署）"]
    SB --> LS["langsmith（可选）"]
    SB --> NO["noop（OBSERVABILITY_BACKEND=none）"]
    LF -.->|"外发副本 · 关掉不影响核心"| EXT["外部 SaaS UI · 告警 · 看板"]
    LS -.-> EXT
```

> 双写的两条腿不对称：内部 DB 是事实本体，外部后端只是投影。所以换后端、关后端都不丢数据，但反过来不成立——外发链路故障不能拖垮 Run（SafeBackend 吞错，见 §6.3）。

### 6.2 适配器契约

```python
# lubanagent/observability/adapters/base.py（示意）
class ObservabilityBackend(Protocol):
    name: str
    def start_trace(self, run: Run) -> TraceHandle: ...
    def emit(self, handle: TraceHandle, event: TraceEvent) -> None: ...
    def end_trace(self, handle: TraceHandle, status: RunStatus, summary: dict | None = None) -> None: ...
```

事件类型定义在 `lubanagent.core.trace.TraceEventType`（`str, Enum`，完整枚举见该文件）：`RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED` / `RUN_CANCELLED` / `STEP_STARTED` / `STEP_COMPLETED` / `MODEL_CALL_STARTED` / `MODEL_TOKEN_DELTA` / `MODEL_CALL_COMPLETED` / `TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED` / `TOOL_CALL_FAILED` / `TOOL_APPROVAL_REQUESTED` / `TOOL_APPROVAL_RESOLVED` / `BUDGET_WARNING` / `BUDGET_EXCEEDED` / `MODEL_FALLBACK`。适配器只负责把这些事件翻译成目标后端的格式。

### 6.3 内置适配器

| 适配器 | 文件 | 选址方式 |
| --- | --- | --- |
| Langfuse 自部署（默认，开箱即用） | `lubanagent/observability/adapters/langfuse.py` | `OBSERVABILITY_BACKEND=langfuse`（缺省） + `LANGFUSE_HOST/PublicKey/SecretKey` |
| LangSmith | `lubanagent/observability/adapters/langsmith.py` | `OBSERVABILITY_BACKEND=langsmith` + `LANGSMITH_API_KEY/PROJECT` |
| No-op（零依赖，关闭外部后端） | `lubanagent/observability/adapters/noop.py` | `OBSERVABILITY_BACKEND=none` |

**缺省是 `langfuse`，不是 `none`**：产品主线是"自带监控开箱即用"，用户克隆起来默认就有 Trace/评测 UI，不应默认关闭。`none` 仅作为可选项，用于不需要外部后端、只用内部 DB 的场景。"无 SaaS 依赖"的价值主张由"内部 DB 始终是 source of truth + 外发可关闭"保证，而非"默认关监控"。

### 6.4 依赖注入点

- **框架侧**：`lubanagent.app.factory.create_app()` 启动时读 `OBSERVABILITY_BACKEND` env，从 `lubanagent.observability.adapters` 注册表取后端实例，注入 `Runtime` 与 `Graph` 执行器。
- **用户侧覆盖**：用户可在 `app/main.py` 调用 `default_registry.register_observability_backend("mycorp", MyCorpBackend())`，框架优先使用用户注册项；这样企业自有 APM/埋点系统可以零框架改动接入。
- **失败隔离**：外部后端写失败不得阻断 Run；适配器内部做异步 + 限流 + 降级，失败仅记日志并落 `export_failures` 表（定义在 `persistence/models/trace.py`）供重试。

### 6.5 Langfuse 自部署进 docker-compose（开箱即用）

"自带监控"的真正含义是用户 `docker compose up` 起来就有监控，不需要单独去装 Langfuse。`docker-compose.yml` 默认带 `app + MySQL + Langfuse（自部署）+ Langfuse 自带 Postgres + mock-biz` 四组服务：

```yaml
# docker-compose.yml（节选，示意）
services:
  app:
    environment:
      OBSERVABILITY_BACKEND: langfuse          # 缺省
      LANGFUSE_HOST: http://langfuse:3000
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY:-}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY:-}
    depends_on: [mysql, langfuse, mock-biz]
  mysql:
    image: mysql:8
  langfuse:                                     # 自部署 Langfuse，克隆即有监控 UI
    image: langfuse/langfuse:2
    ports: ["3000:3000"]
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: change-me-in-prod
      SALT: change-me-in-prod
    depends_on: [langfuse-db]
  langfuse-db:                                  # Langfuse 自己的 Postgres，与业务 MySQL 隔离
    image: postgres:16
  mock-biz:                                     # 客服示例 Mock 业务服务（独立进程，见 §7.5）
    # 客服 Tool 通过 BIZ_BASE_URL 经 HTTP 调用，不直接 import demos/
```

- 不配 key 也能起：Langfuse 首次启动后用户在 UI 创建 project 拿到 public/secret key 回填 `.env` 即可；app 在 key 缺失时自动降级为 `noop`，不阻断启动。
- 想用云版 LangSmith 的用户：把 `langfuse` 与 `langfuse-db` 服务注释掉，`OBSERVABILITY_BACKEND=langsmith` + 填 API key 即可，compose 里不依赖它们。
- 完全不要外部后端：`OBSERVABILITY_BACKEND=none`，只剩内部 DB，最轻量。

### 6.6 两国集成深度不等价（诚实说明）

**Langfuse 与 LangSmith 的 Trace 粒度不等价，切换并非完全无缝：**

- LangSmith 是 LangChain 同家公司产品，对 LangChain/LangGraph 原生最深——LangGraph 的 step 级、节点级、checkpointer 状态变更都能自动出细粒度 trace。
- Langfuse 走 OpenTelemetry 标准协议，LangGraph 的 step 级细节需要 LubanAgent 适配器主动映射事件（见 §6.2 事件类型），粒度取决于适配器覆盖的事件范围，默认略粗于 LangSmith 原生。

后果与承诺：

- 两国都能回答 PRD §8 的"每次 Run 可回答七问"（Agent 版本/模型/Prompt/工具调用/Token/费用/结束原因），因为这些由内部 DB 保证，与外部后端无关。
- 但在 **LangGraph 节点级细粒度可视化、step 状态回放** 这类后端原生能力上，LangSmith 体验更细，Langfuse 取决于适配器映射深度。
- 文档与 README 不得宣传"两国完全等价可切换"；应表述为"核心观测能力两国一致，后端原生的细粒度可视化各有差异，详见适配器能力矩阵"。
- 适配器能力矩阵（哪些事件两国都支持、哪些仅 LangSmith）随实现补齐，见适配器源码 `lubanagent/observability/adapters/{langfuse,langsmith,noop}.py` 注释。

---

## 7. 关键设计决策记录

1. **单仓库多子包，不拆多 PyPI 包**：借鉴 Pydantic AI 的职责切分（核心/评测/图），但 LubanAgent 是脚手架不是库，单仓库内 `lubanagent/` 一个 Python 包 + 子模块足够；拆多包会让"克隆即改"复杂化。
2. **`app/` 为用户领地而非 `lubanagent/modules/`**：若依把业务放 `ruoyi-modules/`（框架命名空间下），用户改动与框架边界模糊；LubanAgent 把 `app/` 独立顶层，物理隔离更彻底，git merge 冲突范围最小化。
3. **扩展点 Protocol 而非继承**：借鉴 Pydantic AI 的 `RunContext[Deps]` + Protocol，用户实现接口而非继承基类，框架与用户代码零 import 耦合。
4. **观测双写、外部可插拔**：借鉴 Idun 的多 provider 适配器，但额外规定"内部 DB 始终是 source of truth"，避免评测/成本功能因外部 SaaS 不可用而失效——这是"无 SaaS 依赖"价值主张的工程兜底。
5. **`demos/` 独立于 `app/`**：客服示例的 Mock 业务服务放 `demos/customer_service/`，`app/agents/customer_service.yaml` + `app/tools/customer_service/` 是"示例的脚手架种子"。用户复制到自己的目录改造，原示例可被上游自由更新而不影响用户业务。
   - **Tool 与 Mock 的连接方式（消除依赖方向歧义）**：客服 Tool（`app/tools/customer_service/`）**不直接 import** `demos/` 里的 Mock 服务——那会违反 §3.2 依赖规则 4（`app → demos` 禁止）。正确方式是 Mock 业务服务作为**独立进程**（FastAPI/HTTP）跑，客服 Tool 通过配置的 `BIZ_BASE_URL`（默认指向 Mock 服务）经 HTTP 调用。这样既保住依赖方向，又正好演示"接真实业务服务的写法"：把 `BIZ_BASE_URL` 从 Mock 换成真实业务系统地址即完成接入，Tool 代码一行不改。
   - 因此 `demos/customer_service/mock_services/` 是一个可独立启动的 HTTP 服务（`docker-compose.yml` 里带一个 `mock-biz` 服务），`app/tools/customer_service/order.py` 的 `query_order` 只认 `BIZ_BASE_URL`，不认 `demos`。
6. **`import-linter` 守卫依赖方向**：依赖规则不靠口头约定，CI 强制检查 `app` 不被 `lubanagent` import、`demos` 不被 `lubanagent`/`app` import。
7. **YAML 不是运行期存储**：F1 决策已定（DB 存储为主），架构上 `app/agents/*.yaml` 仅 `luban init` / `luban export` 用，运行期从不读 YAML——避免"用户改 YAML 以为生效了但其实没生效"的陷阱。
8. **`.lubanagent-upstream` 清单文件**：上游维护者与 CI 双重约束，把"哪些目录上游会动"显式化，用户合并时有明确预期。这是若依没有但 LubanAgent 应该补强的升级契约工具。
9. **交付形态：开源核心 + 商用渠道层**（用户拍板）：Agent 本体（F1-F13）是开源核心（Apache-2.0），完全开源；消息渠道接入（F14）、会话状态机（F15）、出站风控（F16）是独立可拆卸的渠道层模块，**开源版不含、商用版提供**，不进开源仓库。架构硬约束：核心不依赖渠道层（业务系统走 REST/SSE 调用即独立运行，被动模式），渠道层单向依赖核心（`lubanagent-channels → lubanagent` 允许、反向禁止，import-linter 守卫）；核心只在 `extension/protocols.py` 定义 `ChannelConnector`/`SessionStateStore`/`OutputGuard` 三个扩展点，渠道层实现可整体替换或卸载。渠道层不开源的理由：IM/电商渠道接口与规则频繁变更、维护成本高，不宜作开源社区资产，作为商用商品承载该维护。真实需求印证（自研 AI 客服接入主流电商平台方案）：Agent 本体仅占约 1/6 工作量，渠道接入/会话状态/出站风控占 5/6——渠道层即这 5/6 的商品化沉淀。
10. **开发者工作台前端技术栈：React 18 + Vite + TypeScript + Tailwind + shadcn/ui，独立前端工程同仓库**（用户拍板）：前端工程落在 `lubanagent/ui/` 子目录，`npm run dev` 独立起开发（带 mock server），`npm run build` 后由 FastAPI 托管静态资源，生产单进程部署。选型理由：① **Langfuse 前端是 React 开源**，工作台 Trace 可视化（事件时间线、节点树、step 回放）可借鉴其组件模式，换 Vue 等于放弃这层现成参考；② **AI/Agent 生态全是 React**——Vercel AI SDK 的流式 hook（`useChat`）直接用于 Run 页面 SSE 渲染，无需自造；③ **OpenAPI 契约链最顺**——FastAPI `/openapi.json` + `openapi-typescript` + `openapi-fetch` 生成 TS 类型与 client，落地"契约先行"；④ **shadcn/ui 与若依哲学同构**——组件源码复制进仓库而非 npm 黑盒依赖，用户改工作台 UI = 改自己仓库的组件源码，契合"克隆即住、源码可控"。不选 Vue 的理由：若依用 Vue 是 Java 后台管理生态标配，LubanAgent 是 Agent 工具，参照系是 AI 生态不是 Java 后台；"若依式"是形态类比（克隆即住、长期跟上游、领地隔离）不是技术栈照搬，硬选 Vue 会失去 Langfuse + AI SDK 两层现成可复用。不选 Next.js 的理由：工作台是内部调试工具不需 SSR/SEO，引入 Node 运行时徒增部署复杂度。
11. **开发方法论：内置 spec-coding skill**：仓库根 `.claude/skills/spec-coding/` 附带规格驱动开发 skill，克隆即用，是「若依式起步基座」的方法论成色（基座不只给运行骨架，还给开发纪律）。正向从 spec 提取开发单元契约（接口/数据/行为/边界/验收）→编码→strict-validation 闭环校验「代码 vs 契约」；逆向扫描代码回写 `docs/`。skill 无内部依赖、语言无关，既是开发本仓库的纪律，也是用户开发自己 Agent 的可复用方法论。不占 F1-F13 运行期功能，随开源核心附带、不强制使用。

---

## 8. 非功能性要求落地

| 维度 | 要求 | 落地方式 |
| --- | --- | --- |
| 升级安全 | 上游不主动触碰用户领地（CI 拦截 user-owned 命中），显著降低合并冲突 | 物理隔离 + `.lubanagent-upstream` + CI 校验 + CHANGELOG 分类 |
| 可扩展 | 新模型/新 Tool/新 Agent 不改框架 | 扩展点 Protocol + 装饰器自动发现 + Registry |
| 可测试 | Tool/Agent/Graph 可独立单测 | `tool/mock.py` + `luban eval` + FakeModel 工厂 |
| 可观测 | 每次 Run 可回答 PRD §8 七问 | `observability/trace/` + 内部 DB 始终写 |
| 安全 | 权限/审批/金额由代码执行 | `tool/executor.py` 强制校验 + `tool/approval.py` 人工节点 + `graph/` 代码级强制流程 |
| 成本 | 事前估算 + 事中熔断 + 事后归因 | `runtime/budget_guard.py` + `observability/cost/` |
| 部署 | 一键 Docker | `docker-compose.yml`（app + MySQL + Langfuse 自部署 + mock-biz） |
| 无 SaaS 依赖 | 关掉所有外部后端仍可跑 | 观测双写、内部 DB source of truth、`OBSERVABILITY_BACKEND` 可切 `none`（缺省 `langfuse` 自部署，非 `none`） |

---

## 9. 与上位文档的一致性核对

- PRD §4 产品主线"若依式起步基座 + 自带监控" → §2 顶层领地划分（框架/用户/共享）落地"起步基座、可长期迭代"；§6 观测可插拔、默认 Langfuse 自部署落地"自带监控开箱即用"。
- PRD §4 价值主张"全源码、代码优先、无黑盒、无强制 SaaS 依赖" → §6 双写模型（内部 DB 始终 source of truth）+ 外发后端可插拔可关闭（`OBSERVABILITY_BACKEND=none`）。注意："无强制 SaaS 依赖"指**数据主权与可关闭**，不是"默认关监控"——缺省仍是 Langfuse 自部署。
- PRD §6.1 交付范围（F1-F13 + 客服示例 + 最小 RAG，功能不分期） → §2 目录树中 `knowledge/` 保留最小骨架（决策记录 §16.4 首版含最小 RAG）；架构上 `knowledge/` 在框架领地，`app/knowledge_sources/` 在用户领地；运行期可选能力默认不启用，简单 Agent 可只接 Mock 知识源。
- 技术提案 §10.5"一个生态，一个引擎两种用法" → §3.1 `runtime` 与 `graph` 并列，共享 `model`/`tool`/`mcp`/`observability`；二者同跑在 LangGraph 上，`runtime` 用 prebuilt + 治理包装，`graph` 手写图。
- 技术提案 §11 仓库结构建议 → 本文 §2 在其基础上落地为文件级树，并显式区分框架/用户领地（原提案未明确领地边界，本文补齐）。
- 技术提案 §15 风险"示例代码侵入核心" → §3.2 依赖规则 4 + `import-linter` 守卫。
- PRD §1.3 交付形态"开源核心 + 商用渠道层" → §2 目录树顶层加 `lubanagent-channels/`（商用、不进开源仓库）；§3.2 依赖规则 6（渠道层→核心允许、反向禁止）；§5 模块映射 F14-F16 标"渠道层·商用"，扩展点定义在开源核心 `extension/protocols.py`。真实需求（自研 AI 客服接入主流电商平台方案）印证：渠道接入/会话状态/出站风控是客服落地的 5/6 工作量，商品化为渠道层。

---

## 10. 实现状态

本文为架构设计，不包含代码实现。截至当前版本，§1-§9 所述设计与下列落地项均已实现（详见各模块代码与 `CHANGELOG.md`）：

- 扩展点 Protocol 完整方法签名：`lubanagent/extension/protocols.py`（含 async Hook / KnowledgeConnector / 渠道层三扩展点）。
- `import-linter` 配置：仓库根 `importlinter.toml`（CI 守卫依赖方向，含渠道层单向约束）。
- `.lubanagent-upstream` CI 校验：上游 PR diff 命中 `user-owned` glob 即红。
- CLI 命令：`luban init` / `luban eval` / `luban export` / `luban migrate`（`lubanagent/cli/`）。
- Prompt 库一等实体：`prompts`/`prompt_versions` 表 + `PromptRepository` + `/v1/prompts` 六端点（列表/创建/详情/版本列表/发版/版本详情）+ Agent 创建/发版落库前 `resolve_prompt_reference` 解析；工作台 `/prompts` 页面配套。
- DB schema 首版：`lubanagent/persistence/models/` + 仓库根 `migrations/` + `alembic.ini`。
- 开发者工作台前端：`lubanagent/ui/`（React 18 + Vite + TS + Tailwind + shadcn/ui，技术栈见 §7.10）。
