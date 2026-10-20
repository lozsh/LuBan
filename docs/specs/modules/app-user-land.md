<!--
TEMPLATE-LOCK: 本文件章节锁定为以下清单，禁止新增/改名/调序：
1. 定位
2. 接口契约
3. 数据模型
4. 行为（状态机/调用树，均无则写"无"）
5. 边界规则
6. 名词
7. 上下游
所有模块（含子域）统一使用本模板。文件名为真实模块名，放在 modules/ 目录下。
-->

# app-user-land 模块

## 定位

`app/` 是用户领地（架构 §2 顶层领地划分），不是框架契约层：用户克隆仓库后在此装配自己的业务 Agent，随便改、上游不主动触碰（除 `.lubanagent-upstream` 标记的 scaffold-seed 文件）。本模块以内置的**客服示例**（`customer-service-bot` + 3 个分流子 Agent）为脚手架种子，演示"如何用框架能力组装一个完整业务场景"（来源：`app/main.py:1-4`）；另带一个**部门项目问答范本**（`dept-project` Agent + `project-docs` 知识源），演示客服场景之外"纯 RAG 问答 + 防错答守门"的最小完整配置——换一个部门项目只需改 `_DOC_FILES` 文档清单与 Agent YAML（来源：`app/knowledge_sources/project_docs.py:1-7`、`app/agents/dept_project.yaml:1-2`）；再带 4 个**能力展示 Agent**（`cs-intake` 结构化输出 / `cs-notifier` JSON 事件输入 / `product-visual` vision 多模态 + RAG / `cs-followup` 会话摘要记忆 + 费用预算），每个 YAML 头部注释标明演示的能力点，覆盖主示例未涉及的框架能力面。

装配根是 `app/main.py::create_app()`：在框架 `lubanagent.app.factory.create_app()` 基础上，手动注册 `SkillRegistry`、灌入产品 FAQ 与项目文档两个知识源（store/embedder 复用工厂挂载的 `app.state.knowledge_store`/`app.state.knowledge_embedder` 同一实例——embedder 按 `settings.embedding_model` 选型的逻辑在框架工厂 `lubanagent/app/factory.py:82-90`：已配走 `OpenAICompatibleEmbedding`，未配走 `FakeEmbedding`，本模块不再自建）、挂载客服团队自定义路由、把 `cs_team` 挂到 `app.state`（来源：`app/main.py:28-79、62-67`）。

`demos/customer_service/` 是与 `app/` 同级的**共享领地**（`.lubanagent-upstream` 标注 `[shared]`），是独立进程的 Mock 业务服务（订单/退款/产品库），供客服示例 Tool 通过 HTTP 调用，不被 `app/`、`lubanagent/` 反向 import（来源：`demos/customer_service/mock_services/server.py:1-5`、`.lubanagent-upstream:13-14`）。

## 接口契约

### HTTP

#### `app/api/teams.py`（用户领地业务自定义路由，挂载于 `app/main.py:66-68`）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| POST | `/v1/teams/cs-team/runs` | 触发客服分流团队一次编排 Run（`body: {input, thread_id?}`，经 `TeamRunner.run` 执行） | 团队/成员 Agent 未落库 → 框架层错误码（详见 `docs/specs/modules/multiagent.md`）；本路由自身不做额外校验，透传 `TeamRunner.run` 抛出的异常（来源：`app/api/teams.py:40-52`） |

#### `demos/customer_service/mock_services/server.py`（Mock 业务 HTTP 服务，端口 9100，纯 stdlib `http.server`，非框架路由）

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/orders/{order_id}` | 按订单号查订单详情 | 订单不存在 → 404 `{"error": "订单不存在", "order_id": ...}`（来源：`demos/customer_service/mock_services/server.py:122-129`） |
| GET | `/orders?user_key=` | 按 `user_key` 查用户订单列表；不传则返回全部订单 | 无显式错误分支，查无结果返回空列表（来源：`server.py:130-141`） |
| POST | `/refunds` | 提交退款申请（body: `order_id`/`amount`/`reason`） | `amount` 非数字 → 422；订单不存在 → 404；订单不可退（`refundable=false`）或退款金额超订单金额 → 409（来源：`server.py:60-83,160-174`） |
| GET | `/refunds/{refund_id}` | 按退款单号查退款单详情 | 退款单不存在 → 404 `{"error": "退款单不存在", ...}`（来源：`server.py:142-149`） |
| GET | `/products/search?q=` | 按关键词模糊搜产品名/FAQ（`q` 为空返回全部产品） | 无显式错误分支（来源：`server.py:86-94,150-157`） |

### RPC / MQ / Cron

无。

## 数据模型

`demos/customer_service/data/` 与 `app/knowledge_sources/product_faq_seed.json` 均为 **Mock 数据文件，非数据库表**，进程内以 `list[dict]`/`dict` 形式加载到内存（`_State` 类，来源：`demos/customer_service/mock_services/server.py:18-43`）。

### `demos/customer_service/data/orders.json`

订单 Mock 数据，字段（来源：`demos/customer_service/data/orders.json:1-19`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| order_id | str | 订单号，如 `ORD-1001` |
| user_key | str | 用户标识 |
| status | str | 订单状态，如 `shipped`/`pending_ship` |
| refundable | bool | 是否可退（`refund_apply` 前置校验依据） |
| amount | float | 订单金额 |
| product | str | 商品名 |
| created_at | str | ISO 8601 时间戳 |

### `demos/customer_service/data/refunds.json`

退款单 Mock 数据，初始为空数组 `[]`，由 `POST /refunds` 运行期追加写回（来源：`demos/customer_service/data/refunds.json:1`、`server.py:72-83`）。字段：`refund_id`（`RFD-XXXX` 自增序号）、`order_id`、`amount`、`reason`、`status`（固定 `"created"`，无后续状态流转，来源：`server.py:73-80`）。

### `demos/customer_service/data/products.json`

产品 Mock 数据，字段：`product_id`、`name`、`faq`（自然语言 FAQ 文本，供 RAG 切块，来源：`demos/customer_service/data/products.json:1-6`）。

### `app/knowledge_sources/product_faq_seed.json`

结构与 `demos/customer_service/data/products.json` 完全一致（`product_id`/`name`/`faq`），是**同源拷贝**，作为 `product_faq.py` 在 Mock 服务不可达时的本地降级种子，供离线测试跑通（来源：`app/knowledge_sources/product_faq.py:21-45`）。`[TODO: 未找到两份数据的自动同步/校验机制，需人工保持一致]`。

## 行为

### 状态机

无（退款单落库后 `status` 固定为 `"created"`，`demos/customer_service/mock_services/server.py` 未实现状态流转；`[TODO: 未找到退款单状态机定义，若后续版本补充需回填]`）。

### 调用树

#### 客服分流团队装配与触发（复用 `docs/specs/modules/multiagent.md` 已展开的编排拓扑，本节聚焦"如何被 app 装配"）

`cs_team` 在 `app/main.py::create_app()` 装配期一次性构建并挂到 `app.state.cs_team`，由 `app/api/teams.py` 路由从 `request.app.state` 取用；若 `app.state` 未挂载（如单测直接调路由函数），`_get_team` 现场兜底构建（来源：`app/main.py:83`、`app/api/teams.py:30-37`）。

```
create_app()  # app/main.py:28
├── _framework_create_app(settings, registry=default_registry, db, on_startup=_ingest_knowledge)  # 框架装配
│   ├── 工厂内知识库装配：embedder 选型（settings.embedding_model 非空 → OpenAICompatibleEmbedding，
│   │   否则 FakeEmbedding）+ KnowledgeStore + KnowledgeSourceManager 挂 app.state.knowledge_*（factory.py:80-98）
│   ├── lifespan: knowledge_manager.ensure_restored()  # 托管源注册恢复，先于 on_startup（factory.py:103-107）
│   └── on_startup: skill_registry.scan() + ingest_product_faq(store, embedder)
│                                                   + ingest_project_docs(store, embedder)  # replace 重灌（含 ingested_at 时间戳）
├── store = app.state.knowledge_store; embedder = app.state.knowledge_embedder  # 复用工厂同一实例（app/main.py:62-65）
│   ├── make_product_faq_connector(store, embedder)  # 注册 product-faq 知识源
│   └── make_project_docs_connector(store, embedder)  # 注册 project-docs 知识源（dept-project 用，app/main.py:67）
├── app.state.skill_registry = skill_registry
├── app.state.cs_team = build_cs_team()  # app/multiagents/cs_team.py:9
│   └── TeamBuilder("cs-team").supervisor("cs-triage").agent("cs-faq").agent("cs-refund").route(max_delegations=4)
└── app.include_router(teams_router)  # app/api/teams.py:15
    └── POST /v1/teams/cs-team/runs → _get_team(request) → TeamRunner(...).run(team, input, thread_id)
        # 具体 supervisor 决策/委派/汇总细节见 multiagent.md
```

#### 退款审批流程（`app/graphs/refund_flow.py::refund_flow_builder`，确定性 LangGraph 图，`@graph` 装饰经 `app.graphs` 扫描入口自动注册，来源：`refund_flow.py:1-11,97-99`）

Agent 侧默认不直接跑本图，而是 `cs-refund`/`customer-service-bot` 经 Tool `refund_apply` 完成退款（Tool 内置审批闸门）；本图是**独立的确定性流程**示例，人工审批节点在"金额计算之后、执行退款之前"，当 `amount > REFUND_APPROVAL_THRESHOLD`（单点配置 `app/config/refund_policy.py`，当前 500.0 元）时插入 `builder.human("approve", ...)` 节点阻塞等待人工决策，否则直接进入执行退款（来源：`refund_flow.py:82-92`）。

```
refund_flow (GraphBuilder, entry=check_eligibility)  # refund_flow.py:76-94
├── check_eligibility  # 资格校验
│   └── 经 default_registry.get_tool("query_order").handler(order_id) 查单
│       └── order.refundable == False → raise LubanError(TOOL_EXECUTION_FAILED)  # 终止
├── compute_amount  # 金额计算：amount = state.amount or order.amount；needs_approval = amount > REFUND_APPROVAL_THRESHOLD（refund_flow.py:45，口径 > 非 >=）
├── [条件 _needs_approval] 分支
│   ├── True  → approve (human 节点，prompt=f"退款金额 >{REFUND_APPROVAL_THRESHOLD:g} 元，需要人工审批")  # 人工审批节点
│   │            └── 审批通过后 → execute_refund
│   └── False → execute_refund  # 直接跳过审批
├── execute_refund  # 经 default_registry.get_tool("refund_apply").handler(order_id, amount, reason)
│   └── 结果 → {"refund_result": ..., "refunded": True}
└── notify  # 构造用户可见摘要："退款已提交：订单 xxx，金额 xxx 元，退款单号 xxx"
```

注：图内 `check_eligibility`/`execute_refund` 步骤直接调 Tool 的 `handler`（经 `default_registry.get_tool(...)`），刻意绕开 Agent 层但不绕开 Tool 治理包装（审批闸门仍生效），代码注释显式说明这一设计取舍（来源：`refund_flow.py:13-15,23-27,52-55`）。

## 边界规则

- **依赖方向约束（本模块最重要的边界）**：`app/tools/customer_service/order.py`、`refund.py` 与 `app/knowledge_sources/product_faq.py` **禁止 `import demos/*`**，只能经配置项 `BIZ_BASE_URL`（`lubanagent.config.get_settings().biz_base_url`，缺省 `http://localhost:9100`）走 HTTP 调用 Mock 服务；`demos/customer_service/mock_services/server.py` 文件头注释明确"Tool 只经 HTTP 认 BIZ_BASE_URL，禁止 import 本模块（领地隔离）"（来源：`app/tools/customer_service/order.py:3,12-17`、`app/tools/customer_service/refund.py:3,12-17`、`server.py:4`）。架构文档同步约束 `app/*` 与 `lubanagent/*` 都不能反向依赖 `demos/*`（来源：`docs/design-docs/LubanAgent-architecture-design.md:247`）。
- **知识源降级不跨领地读文件**：`app/knowledge_sources/product_faq.py` 优先经 HTTP 拉 Mock 服务数据，失败时降级读**本领地内**的 `app/knowledge_sources/product_faq_seed.json`，显式注释"不跨领地读 demos/ 文件"（来源：`product_faq.py:21-22,32-45`）。
- **知识源灌入失败不阻断启动**：`_ingest_knowledge` 钩子内 catch 所有异常仅告警，不影响 `create_app()` 返回（来源：`app/main.py:46-53`）。
- **代码种子源在工作台知识库为"内置源"**：project-docs/product-faq 不入 `knowledge_sources` 表，`GET /v1/knowledge/sources` 列表里以 `managed=False` 合并展示（参数不可改、不可删、不可重切）；工作台经 `POST /v1/knowledge/sources` 创建的托管源与其共存于同一 `KnowledgeStore`/embedder 向量空间（来源：`app/main.py:62-67`、`lubanagent/knowledge/manager.py:113-138`）。
- **知识源灌入打 `ingested_at` 时间戳**：`ingest_product_faq` 每块 metadata 写入 UTC ISO `ingested_at`（`app/knowledge_sources/product_faq.py:65-66`），是框架 `max_age_days` 超龄过滤（防错答守门）的判定依据；启动钩子 replace 重灌同时保证 `FakeEmbedding` 维度升级（64→512→2048）后存量向量自动重建。
- **部门项目问答范本（project-docs 知识源 + dept-project Agent）**：`app/knowledge_sources/project_docs.py` 把五份核心文档（PRD / 用户手册 / 技术方案 / 架构设计 / AGENTS.md，清单固定在 `_DOC_FILES`，缺失文件跳过不炸启动，L36-38）灌入 KnowledgeStore（source=project-docs，`chunk_text(chunk_size=300, overlap=30)`，整源 replace；metadata 记 `doc` 来源文件名供 `[n]` 引用溯源 + `ingested_at`，L41-70）；`app/agents/dept_project.yaml`（dept-project）是纯 RAG 问答 Agent——`tools: []` + `brain: smart` + `memory: session` + 防错答守门（`score_threshold: 0.24` 为 FakeEmbedding(2048 维) 对该语料的实测分界：正例 ≥0.24、无关 ≤0.235；换 embedding 或扩文档需重测，YAML 注释 L30-32）。换一个部门项目 = 改 `_DOC_FILES` 清单 + 复制改写该 YAML。`dept_project.yaml`、`project_docs.py` 连同 `app/main.py` 的三行装配（import + connector + ingest）均已列入 `.lubanagent-upstream` `[scaffold-seed]`——范本整体随上游走，用户原地改会与上游冲突，官方改法是复制成新文件再改。
- **评测数据集**：`app/datasets/cs_basic.yaml`（scaffold-seed）面向带工具的客服 Agent，`unknown_reject` 用例走 judge 判模型层拒答；`app/datasets/cs_knowledge.yaml` 面向纯 RAG 问答 Agent（cs-faq），`unknown_refuse` 用例 `expect_refusal: true` 走事件级硬指标（事件流须含 `knowledge_no_hit`，不依赖 judge）——两者注释明确分工（来源：`app/datasets/cs_basic.yaml:23-29`、`app/datasets/cs_knowledge.yaml`）。`cs_knowledge.yaml` 与 `cs_basic.yaml` 同列 `.lubanagent-upstream` `[scaffold-seed]`，随上游走。
- **退款审批阈值单点配置（风险 R9 修复）**：「退款金额 > N 元触发人工审批」的口径此前三处硬编码（Tool 描述 / Agent 指令 / 图分支），现统一为单点常量 `REFUND_APPROVAL_THRESHOLD = 500.0`（`app/config/refund_policy.py:19`，判定口径是 `>` 非 `>=`）。Tool（`app/tools/customer_service/refund.py:9,26`，描述文本 `f"...>{REFUND_APPROVAL_THRESHOLD:g}元触发人工审批..."`）与图（`app/graphs/refund_flow.py:10,45,87`，分支判定与 human 节点 prompt）均 import 该常量，改一处即改两处。Agent 指令是 YAML 静态文本无法 import——改常量时必须同步修改 `customer_service.yaml`、`cs_refund.yaml` 指令中的阈值数字，守卫测试 `tests/demos/customer_service/test_refund_policy.py` 校验 YAML 数字、Tool 描述、图 prompt 与常量逐字一致（`> {500:g} 元` 等短语断言），漏改即测试红。Agent 指令位置：`customer_service.yaml`、`cs_refund.yaml`。
- **`.lubanagent-upstream` 清单对本模块的种子/用户领地边界**（来源：`.lubanagent-upstream:38-79`）：
  - `[scaffold-seed]`（首次克隆提供可跑内容，上游仅做破坏性变更时才动，用户原地改会与上游冲突）：`app/__init__.py`、`app/**/__init__.py`、`app/main.py`、`app/config/.env.example`、`app/config/refund_policy.py`、四个客服 Agent YAML（`customer_service.yaml`/`cs_faq.yaml`/`cs_refund.yaml`/`cs_triage.yaml`）、部门项目问答范本两件（`dept_project.yaml` + `project_docs.py`）、四个能力展示 Agent（`cs_intake.yaml`/`cs_notifier.yaml`/`product_visual.yaml`/`cs_followup.yaml`）、`app/tools/customer_service/`、`app/graphs/refund_flow.py`、`app/multiagents/cs_team.py`、`app/skills/refund-sop/`、`app/knowledge_sources/product_faq.py`、`app/knowledge_sources/product_faq_seed.json`、`app/datasets/cs_basic.yaml`、`app/datasets/cs_knowledge.yaml`、`app/api/teams.py`。
  - `[user-owned]`（上游永不主动触碰，用户新增文件天然落在此类，不会冲突）：以上种子文件之外的 `app/agents/*.yaml`、`app/tools/*/`、`app/graphs/*.py`、`app/multiagents/*.py`、`app/skills/*/`、`app/knowledge_sources/*.py`、`app/datasets/*.yaml`、`app/api/*.py`、`app/extensions/`、`app/config/` 下除 `.env.example`（seed）外全部文件——`refund_policy.py` 虽命中该 glob 但 seed 优先（用 glob 否定语法 `!(...)` 排除种子文件名）。
  - 守卫脚本 `scripts/check_upstream_guard.py`：CI 审上游提交 diff（`--base`，默认 `HEAD~1`）命中 user-owned 即拒；`--include-worktree` 本地模式额外纳入未提交 tracked 修改与未跟踪新文件（CI 语义不变）；user-owned 与 scaffold-seed 重叠时以 seed 为准（来源：`scripts/check_upstream_guard.py`、`tests/test_upstream_guard.py`）。
  - 官方建议改法（来源：`docs/design-docs/LubanAgent-architecture-design.md:299`）：**不原地改**客服示例种子文件，复制 `app/agents/customer_service.yaml` 为新文件再改，保持零冲突。
  - `demos/`、`tests/` 整体标注 `[shared]`（上游为主，用户少改，非本模块 `[user-owned]` 范畴）。
- `app/config/.env.example`（4806 字节，含零配置兜底默认值）已随仓库交付，`README.md` 的 `cp app/config/.env.example .env` 步骤可直接执行（来源：`app/config/.env.example`）。`app/config/` 下除 `.env.example`（seed）外的其余路径（含历史提及的 `settings.local.py`）均属 `[user-owned]`；框架加载链是 `.env → .env.{ENV} → 真实环境变量`（来源：`lubanagent/config/settings.py`），不自动加载任何 `app/config/*.py`；用户自建 config 文件需在 `app/main.py` 自行装配，守卫只保证上游不触碰它们。

## 名词

| 术语 | 语义 |
|---|---|
| 装配根（scaffold entry） | `app/main.py::create_app()`：在框架 `create_app` 基础上手动注册 Skill/知识源/自定义路由的用户领地入口函数（来源：`app/main.py:1,24`） |
| BIZ_BASE_URL | `lubanagent.config.Settings.biz_base_url` 对应的环境变量，客服 Tool 与知识源装配用它定位 Mock 业务服务地址，缺省 `http://localhost:9100`（来源：`app/tools/customer_service/order.py:12-17`、`lubanagent/config/settings.py:38`） |
| mock-biz | `demos/customer_service/mock_services/` 提供的独立进程 HTTP 服务的对外称呼，`docker-compose.yml` 中的服务名，端口 9100（来源：`demos/customer_service/mock_services/server.py:190`、`docs/design-docs/LubanAgent-architecture-design.md:566`） |
| scaffold-seed（脚手架种子） | `.lubanagent-upstream` 清单中的分类：首次克隆即提供可跑内容的文件，上游仅在破坏性变更时才会修改，用户若原地改动会在合并上游时产生冲突（来源：`.lubanagent-upstream:5,38-64`） |
| 客服分流团队 / cs-team | `app/multiagents/cs_team.py::build_cs_team()` 构建的多 Agent 编排拓扑：supervisor=cs-triage，成员=cs-faq/cs-refund，经 `app/api/teams.py` 暴露触发入口（来源：`cs_team.py:9-19`） |
| 退款流水线 / refund_flow | `app/graphs/refund_flow.py` 定义的确定性 LangGraph 图，独立于 Agent 对话流程，演示图编排能力（来源：`refund_flow.py:1-5`） |
| 部门项目问答范本 / dept-project | `app/agents/dept_project.yaml` + `app/knowledge_sources/project_docs.py` 组成的"纯 RAG + 防错答守门"最小完整配置：知识源灌部门项目文档、Agent 只依据检索片段带 `[n]` 引用作答、无关问题在守门处短路拒答（来源：`app/agents/dept_project.yaml:1-37`、`app/knowledge_sources/project_docs.py:1-7`） |

## 上下游

**上游依赖**（本模块消费外部数据）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent/`（框架全部能力） | `lubanagent.app.factory.create_app`、`lubanagent.extension.tool/graph/default_registry`、`lubanagent.knowledge.*`、`lubanagent.skill.SkillRegistry`、`lubanagent.multiagent.Team/TeamBuilder/TeamRunner`、`lubanagent.graph.GraphBuilder`、`lubanagent.config.get_settings`、`lubanagent.core.*` | `app/` 单向依赖 `lubanagent/` 的公开 API 完成装配、Tool/Graph 注册、审批治理、错误码定义（来源：`app/main.py:11-16`、`app/tools/customer_service/order.py:8-10`、`app/graphs/refund_flow.py:9-11`） |
| `demos/customer_service/mock_services/`（独立 HTTP 服务，非 import 依赖） | `GET /orders/{id}`、`GET /orders?user_key=`、`POST /refunds`、`GET /refunds/{id}`、`GET /products/search` | `app/tools/customer_service/order.py`、`refund.py`、`app/knowledge_sources/product_faq.py` 经 `httpx` 调用上述端点获取订单/退款/产品数据（来源：`order.py:36`、`refund.py:39`、`product_faq.py:35`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `demos/customer_service/mock_services/` | `POST /refunds` | `refund_apply` Tool 提交退款申请，触发 Mock 服务写回 `demos/customer_service/data/refunds.json`（来源：`server.py:72-83`） |
| `lubanagent` 治理层（审批/Trace/预算） | Tool 调用（`query_order`/`refund_apply`）、Team/Graph Run | 客服示例的每次 Agent 调用、Team 编排、Graph 执行均落入框架统一的 Run/子 Run 治理（预算/审批/Trace），具体机制见 `docs/specs/modules/multiagent.md`、`docs/specs/modules/graph.md`、`docs/specs/modules/tool-mcp.md`（来源：`refund_flow.py:23-27,52-55` 引用 `default_registry.get_tool` 的治理包装 handler） |
