# 契约 D5-1：`demos/customer_service` + `app/` seeds（客服示例纵切）

> 来源：开发计划 D5-1、用户手册 §4.3/§12/§13、架构设计 §2/§4.3、技术方案 §10.5（退款流水线形态）。
> 前置：D1-3/D1-4/D1-5/D1-8、D2-1、D3-1/D3-3/D3-4/D3-5、D3-int 全部完成。

## 范围

- `demos/customer_service/`：`mock_services/`（订单/退款/产品库 HTTP Mock 服务）、`data/`（种子数据 JSON）、`README.md`、`docker-compose.override.yml`（可选：mock-biz 服务片段）
- `app/agents/customer_service.yaml`、`app/agents/cs_faq.yaml`、`app/agents/cs_refund.yaml`、`app/agents/cs_triage.yaml`
- 能力展示 Agent 四件（YAML 头部注释标明演示点）：`app/agents/cs_intake.yaml`（结构化输出 schema）、`app/agents/cs_notifier.yaml`（JSON 事件输入）、`app/agents/product_visual.yaml`（vision 槽位 + multimodal 输入 + RAG 守门）、`app/agents/cs_followup.yaml`（session-summary 记忆 + budget_cost 费用预算）
- `app/tools/customer_service/`：`order.py`、`refund.py`、`__init__.py`
- `app/graphs/refund_flow.py`
- `app/multiagents/cs_team.py`
- `app/skills/refund-sop/SKILL.md`
- `app/knowledge_sources/product_faq.py`（FAQ 知识源装配）
- `app/datasets/cs_basic.yaml`
- `app/prompts/`（如需抽 Prompt 片段，可选）
- `app/main.py`（装配根：create_app + 注册扩展，scaffold-seed）
- `tests/demos/customer_service/`（示例回归测试）

## 契约决策

1. **命名约定**（开发计划钉死）：YAML 文件名用下划线（`customer_service.yaml`），文件内 `name` 字段即 Agent key 用连字符（`customer-service-bot`）；分流团队三 Agent：`cs-triage`/`cs-faq`/`cs-refund`。
2. **Tool 只认 `BIZ_BASE_URL`**：order.py/refund.py 经 httpx 调 Mock 服务（默认 `http://localhost:9100`），**禁止 import demos/**（领地规则）；Mock 服务是独立进程（FastAPI 或纯 stdlib http.server——决策：stdlib `http.server` 单文件，零额外依赖，`python -m demos.customer_service.mock_services` 可起）。
3. **Mock 数据**：`data/orders.json`（≥5 单，含"已发货/未发货/可退/不可退"形态）、`data/refunds.json`（空初始，退款回写）、`data/products.json`（≥8 条产品 FAQ 素材，兼作知识库语料）。
4. **退款流水线**（技术方案 §10.5 形态）：资格校验（step)→金额计算（step)→when(>500→human 审批，否则直退)→执行退款（step)→通知（step)；用 D3-4 GraphBuilder 实现，注册 `@graph`。
5. **分流团队**：cs-triage（supervisor）+ cs-faq + cs-refund 三 Agent YAML + `app/multiagents/cs_team.py` 用 D3-5 TeamBuilder 构建并在 `app/main.py` 装配（注册入口或经 API 触发——v1 提供 `POST /v1/teams/cs-team/runs`? 不——**决策**：装配根把 team 挂在 `app/api/teams.py` 自定义路由（用户领地业务路由，架构 §4.1 允许），不动框架 api）。
6. **SOP Skill**：`refund-sop/SKILL.md` 标准格式（frontmatter name/description + 正文 SOP 步骤 + `scripts/refund_check.py` 演示脚本：校验订单号格式）。
7. **评测集**：`cs_basic.yaml` ≥5 case（覆盖 FAQ 命中/查单工具调用/退款大额审批/情绪安抚 judge/未知问题拒答）。
8. **30 分钟跑通**（验收原文口径）：README 给出 `cp .env.example .env`→`alembic upgrade head`（或 luban migrate）→起 mock-biz→`luban init`→起服务→curl SSE 全流程；测试侧以回归测试自动化该流程的关键步骤（不起 docker）。

## 数据契约

### customer_service.yaml（主 Agent，完整定义见 `app/agents/customer_service.yaml`）
```yaml
name: customer-service-bot
description: 售后客服：查单/退款/FAQ，大额退款走审批
instruction: |
  # 角色
  你是「小鲁」，本店售后客服，处理订单查询、退款申请、退换货政策与产品咨询。
  ……（结构化提示词：角色/服务流程/工具使用规则/知识与表达/边界，约 800 字。
  关键行为锚点：先安抚情绪再处理；查订单用 query_order；退款用 refund_apply
  （>500 元触发人工审批，不承诺立即到账）；知识库带 [n] 来源编号；
  改地址/物流/发票等超出能力范围 → 如实说明并转人工，不承诺。）
brain: smart
fallbacks: [fast]
tools: [query_order, refund_apply]  # refund_flow 是 @graph 图非 Tool，经 GraphRunner/flow_as_tool 装配，不入 tools 列表（批次 H 审稿裁决：框架 agent_loop 仅认 registry.has_tool，混列会触发 AGENT_VERSION_UNRUNNABLE）
skills: [refund-sop]
knowledge: [product-faq]
memory: session
limits: { max_steps: 12, budget_tokens: 50000, timeout_sec: 120 }
io_contract: { input: text, output: text }
```

### Mock 服务端点（stdlib http.server，端口 9100）
- `GET /orders/{order_id}` → 200 订单 JSON / 404
- `GET /orders?user_key=` → 列表
- `POST /refunds` {order_id, amount, reason} → 201 退款单（校验订单可退：未发货或可退标志；金额 ≤ 订单金额）
- `GET /refunds/{id}` → 退款单
- `GET /products/search?q=` → 产品库模糊匹配列表

### Tool 定义（@tool 装饰）
- `query_order(order_id: str) -> dict`：risk=low、idempotent=True、timeout=10
- `refund_apply(order_id: str, amount: float, reason: str) -> dict`：risk=high、approval_required=True、timeout=15、idempotent=False
- 两 Tool 从 env `BIZ_BASE_URL` 读地址（缺省 http://localhost:9100）；连接失败 → LubanError(TOOL_EXECUTION_FAILED, retryable=True)

## 行为契约（验收场景 = 回归测试）

1. **改定义即时生效**：改库中 Agent 的 instructions → 下一次 Run 行为变化（测试：API 改定义 → 再跑 → fake 模型收到新 prompt）。
2. **高风险审批**：`refund_apply(>500)` 触发 approval 事件 + WAITING_APPROVAL，`POST /v1/approvals/{id}/decide` 批准后走完（Mock biz 收到退款单）。
3. **预算熔断**：budget_tokens 小值 → Run 终止 end_reason=BUDGET_EXCEEDED。
4. **Trace 回放**：完成 Run 后 `/v1/traces/{run_id}` 能回答七问。
5. **评测**：`luban eval run customer-service-bot@v1 --dataset app/datasets/cs_basic.yaml` 出报告（FakeLLM 下断言可确定性通过）。
6. **退款流水线**：refund_flow 小额直退走完；大额 interrupt → resume(approved) 走完（Mock biz 各收到一次退款单）。
7. **多 Agent 分流**："退货政策" → triage 委派 cs-faq；"我要退款" → 委派 cs-refund。
8. **RAG**：产品问题命中 FAQ 片段带引用编号（FakeEmbedding）。
9. **Skill**：skill_load(refund-sop) 返回 SOP 正文；scripts/refund_check.py 受控执行。
10. **零配置兜底**：不起 mock-biz 时核心照常起（Tool 失败显式报错不炸服务）。

## 边界契约

- `app/*` 只 import `lubanagent/*`（lint-imports 领地守卫）；`demos/*` 不被 app/lubanagent import（Tool 走 HTTP）。
- 示例是 scaffold-seed：注释引导用户"复制改造，不原地改"。
- Mock 服务不模拟"真实平台全量语义"，仅支撑验收场景（不过度建设）。

## 验收标准（须实跑）

1. `pytest tests/demos/customer_service/` 全绿（场景 1-9 自动化；mock-biz 以 fixture 起子进程或线程）。
2. 全量回归绿；`ruff check app demos tests/demos` 无错；`lint-imports` 无 broken（app→demos 禁止的守卫实测）。
3. README 流程逐步可复现（主 agent 逐步走一遍：migrate→mock-biz→init→uvicorn→curl SSE→decide→trace）。

## 存疑项与决策

1. Mock 服务用 stdlib 单文件（决策 2）——零依赖，`docker-compose` 的 mock-biz 片段给 override 文件不侵入主 compose（主 compose 属 D1-9）。
2. 团队触发入口走用户领地自定义路由 `app/api/teams.py`（决策 5）——演示"业务自定义路由"用法（架构 §4.1）。
3. 评测在 FakeLLM 下的确定性：cs_basic.yaml 的断言围绕工具调用与关键字，judge 用 FakeLLM 脚本分数（保证 CI 可重复）。
