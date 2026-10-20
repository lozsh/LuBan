# 评测体系

> 本页讲测试集、四类断言、`expect_refusal` 硬指标、录制重放与 `--compare` 对比回归。

**是什么**：测试集（YAML 管理）→ 跑评测 → 断言与评分 → 对比报告。断言四类：工具调用断言（该调的调了没有、参数对不对）、输出断言（关键字/结构）、LLM-as-judge（语义打分）、**拒答断言**（防错答硬指标）；支持历史 Trace **录制重放**，以及模型/Prompt 切换前后的 A/B 对比与回归门禁。

**定位口径**：这是"**可用的评测起点**"——evals 是全行业未解难题，LubanAgent 给框架、给流程、给数据，不承诺"自动证明 Agent 可靠"。

**怎么用**（示意）：

```yaml
- case: 查不到订单要安抚并给替代方案
  input: "我的订单 12345 怎么还没到"
  expect_tools: [query_order]
  judge: { criteria: "先安抚情绪，再给解决方案", pass_score: 4 }
- case: 知识库没覆盖的问题必须拒答
  input: "你能帮我改一下收货地址吗"
  expect_refusal: true   # 事件流必须出现 knowledge_no_hit；没拒答 = 错答，直接 FAIL
```

`expect_refusal: true` 是确定性判定（看事件不看文本，不依赖 judge）：开了守门的 RAG Agent 在知识没覆盖时必须走短路拒答，评测用它把"错答率"钉成可回归的硬指标——报告里的错答率（应拒答用例中未拒答的比例）目标永远是 0。守门字段见[知识与记忆](./knowledge-memory.md)。

```bash
luban eval run customer-service-bot@3 --dataset datasets/cs-basic.yaml --compare 2
```

跑完的报告自动落库：工作台「评测」页可查报告列表（通过率/错答率等聚合指标，可按数据集/Agent 过滤）与逐 case 明细（断言四类——工具/输出/judge 评分/拒答——如实展示）；REST 侧对应 `GET /v1/evals`（列表）与 `GET /v1/evals/{result_id}`（详情）。`--save` 的 JSON 报告文件是跨环境可移植的基线载体，`--compare` 对比（版本号重跑或 JSON 基线文件）走内存/文件链路，不依赖数据库。
