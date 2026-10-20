# 客服示例（LubanAgent 第一个完整验证场景）

> 本目录是 LubanAgent 若依式基座的"自带示例"：克隆仓库后 30 分钟跑通一个售后客服 Agent。
> 客服是示例不是产品——演示如何用基座组装一个完整业务场景，不承诺覆盖真实平台全量语义。
> 想系统学习（含概念讲解与练习，从零造一个自己的 Agent）→ [新手教程](../../website/tutorial/index.md)。

## 是什么

一个售后客服 Agent，能力覆盖：查订单、退款（大额走审批）、产品 FAQ（RAG）、情绪安抚。
包含分流团队（多 Agent）与退款流水线（确定性图编排）两种编排形态。

## 为什么有这个示例

证明基座成色：单 Agent / 多 Agent / 图编排 / 知识库 / Skill / 评测 / 审批 / Trace 全链路跑通。
用户照着改自己的业务即可，不需要从零搭。

## 怎么用

### 30 分钟跑通

先说清楚零密钥的边界：**不配任何模型 Key，你可以把全套服务跑起来、完成对话、看到事件流与 Trace——但回复是 FakeLLM 的固定台词，查订单、答 FAQ、走审批这些需要真智力的行为不会发生**（FakeLLM 不做工具决策）。要完整体验下面全部九步，需要一个大模型 API Key（DeepSeek 等，约 10 元额度足够）。

```bash
# 1. 配置（缺省 SQLite + FakeLLM 兜底；接真模型在 .env 填厂商 Key，见第 2 步后说明）
cp app/config/.env.example .env

# 2. 迁移
luban migrate

# 3. 启动 Mock 业务服务（订单/退款/产品库，端口 9100）
python -m demos.customer_service.mock_services &

# 4. 导入 Agent 定义（种子模型 + Agent YAML 落库）
luban init

# 5. 启动 API 服务
uvicorn lubanagent.app.factory:create_app --factory  # 或用 app.main:create_app

# （接真模型：.env 填 DEEPSEEK_API_KEY=sk-xxx 后，经工作台"模型"页或
#  PUT /v1/models + PUT /v1/brain-mappings 把模型绑到 smart 槽位——
#  以下 6-9 步的完整行为依赖真模型）

# 6. 跑一次对话（SSE 流式）
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "帮我查一下订单 ORD-1001"}'

# 7. 大额退款触发审批（输入需自带退款原因，模型按 SOP 会先查证）
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "我要退订单 ORD-1006 的款，899 元，质量有问题，键盘按键失灵"}'
# → 收到 tool.approval.requested 事件，记下 approval_id

# 8. 批准审批
curl -X POST http://localhost:8000/v1/approvals/{approval_id}/decide \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "decided_by": "demo"}'

# 9. 查 Trace 回放
curl http://localhost:8000/v1/traces/{run_id}
```

### 跑评测

需要真模型（judge 用例依赖 SMART 槽位的真实模型；FakeLLM 下行为断言无法通过）：

```bash
luban eval run customer-service-bot --dataset app/datasets/cs_basic.yaml
# expect_tools 按「调用意图」判定；断言依赖执行结果（如退款单号）时加 --auto-approve
# （审批型工具会真实执行——确认数据集指向 Mock/受控环境）
```

### 多 Agent 分流团队

```bash
curl -X POST http://localhost:8000/v1/teams/cs-team/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "退货政策是什么"}'
# → triage 委派 cs-faq

curl -X POST http://localhost:8000/v1/teams/cs-team/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "我要退款"}'
# → triage 委派 cs-refund
```

### 退款流水线（确定性图编排）

退款流水线注册为 `refund_flow` 图，经 Tool refund_apply 在 Agent 内被调用，
或直接经 GraphRunner 跑（测试见 `tests/demos/customer_service/`）。

## 文件结构

```
demos/customer_service/
  data/                 # 种子数据（订单/退款/产品 FAQ）
  mock_services/        # stdlib HTTP Mock（端口 9100）
（mock-biz 容器编排见仓库根目录 docker-compose.yml）
app/
  agents/               # 4 个 Agent YAML（主 bot + 3 分流成员）
  tools/customer_service/  # query_order + refund_apply（@tool）
  graphs/refund_flow.py    # 退款流水线（@graph）
  multiagents/cs_team.py  # 分流团队（TeamBuilder）
  skills/refund-sop/       # SOP Skill（SKILL.md + 脚本）
  knowledge_sources/product_faq.py  # FAQ RAG 装配
  datasets/cs_basic.yaml   # 评测数据集
  api/teams.py             # 团队自定义路由
  main.py                  # 装配根
```

## 边界

- Mock 服务仅支撑验收场景，不模拟真实平台全量语义。
- 知识库用 FakeEmbedding（确定性 hash 向量），不承诺生产检索质量。
- 客服示例是演示用，不是产品级客服系统。
