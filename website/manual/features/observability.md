# 运行观测

> 本页讲每次 Run 的完整 Trace 记录了什么、为什么它是治理主线的数据地基、怎么查怎么看。

**是什么**：每次 Run 落一条完整 Trace：Agent 定义版本、实际模型、最终 Prompt、每一次工具调用（入参/出参/耗时）、Token 用量、费用、结束原因（完成/超步/超预算/取消/错误）。

**为什么需要**：排障靠它，评测素材靠它（[评测体系](./evals.md)必须基于真实运行观测），成本归因靠它（见[成本治理](./cost.md)）。一个 Trace 三种用途，是治理主线的数据地基。

**怎么用**：API 按会话/按用户标签/按时间查 Trace（`user_id` 是 Run 创建时透传的观测标签，见 [Agent Runtime](./runtime.md)）；[开发者工作台](./workbench.md)可视化查看，Trace 列表与详情直接显示每次 Run 的会话 ID 和用户标签；数据留在自己库里，无 SaaS 依赖。

**边界**：观测后端（Langfuse 自部署默认 / LangSmith / 关闭）是可切换的旁路，内部 DB 始终是 source of truth；怎么换见[部署运维](../deploy.md)与[改造指南](../customization/overview.md)。
