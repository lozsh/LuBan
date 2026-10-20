# 多 Agent 协作

> 本页讲什么时候需要多 Agent、supervisor 委派拓扑怎么写，以及"编排 Run 与单 Agent Run 同治理"的关键设计。

**什么时候需要**：单个 Agent 不够用——复杂任务要分工（一个规划、一个检索、一个执行），或要把多个专精 Agent 编排成一条协作链。这种事在[图编排](./graphs.md)之上再做一层：把"节点 = Agent"组合成 supervisor / 子 Agent 委派拓扑。

**是什么**：基于 LangGraph 的受控多 Agent 编排——supervisor 决定下一步交给哪个子 Agent、子 Agent 跑完回报、共享状态在节点间流转、可条件路由与人工暂停。**关键：编排 Run 与单 Agent Run 享同一套治理**——每个子 Agent 调用都进 Trace（绑定各自定义版本与实际模型）、整条编排受预算熔断、高风险节点可人工确认、编排可被评测回归与录制重放。不另起一套治理。

**怎么用**（示意）：

```python
from lubanagent.multiagent import TeamBuilder

team = (
    TeamBuilder("cs-team", description="客服分流团队")
    .supervisor("cs-triage")              # supervisor 是已注册的 Agent key（带自己的大脑）
    .agent("cs-faq", description="退货政策咨询")
    .agent("cs-refund", description="退款执行")
    .route(max_delegations=4)             # 只设委派上限；路由由 supervisor Agent 的 LLM 决定
    .build()
)
```

**边界**：多 Agent 协作是可选能力——能单 Agent 解决的不要硬凑多 Agent（白付协调成本与 token）。代码优先，不做可视化编排平台。
