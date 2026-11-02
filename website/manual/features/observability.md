# 运行观测

> 本页讲每次 Run 的完整 Trace 记录了什么、为什么它是治理主线的数据地基、怎么查怎么看。

**是什么**：每次 Run 落一条完整 Trace：Agent 定义版本、实际模型、最终 Prompt、每一次工具调用（入参/出参/耗时）、Token 用量、费用、结束原因（完成/超步/超预算/取消/错误）。

**为什么需要**：排障靠它，评测素材靠它（[评测体系](./evals.md)必须基于真实运行观测），成本归因靠它（见[成本治理](./cost.md)）。一个 Trace 三种用途，是治理主线的数据地基。

**怎么用**：API 按会话/按用户标签/按时间查 Trace（`user_id` 是 Run 创建时透传的观测标签，见 [Agent Runtime](./runtime.md)）；[开发者工作台](./workbench.md)可视化查看，Trace 列表与详情直接显示每次 Run 的会话 ID 和用户标签；数据留在自己库里，无 SaaS 依赖。

**边界**：观测后端（Langfuse 自部署默认 / LangSmith / 关闭）是可切换的旁路，内部 DB 始终是 source of truth；怎么换见[部署运维](../deploy.md)与[改造指南](../customization/overview.md)。

## 外部 Watchdog——卡死 Run 的独立巡检

**是什么**：一个独立于执行引擎的定时巡检回路（环境变量 `WATCHDOG_ENABLED=true` 开启，默认关闭），定期扫描 RUNNING 状态的 Run：事件断流超过阈值（`stale_events`）或运行时长超上限（`long_running`）时产生告警——告警事件落入目标 Run 的 Trace（工作台 Trace 详情可见），`GET /v1/watchdog/alerts` 可查。

**为什么需要**：执行引擎自己不会报告自己卡死了。Watchdog 在主系统之外运行，引擎整体挂住时它仍然活着——这是"外部"两个字的意义，也是排障时区分"跑得慢"和"已经死了"的直接依据。

**怎么用**（环境变量，均有默认值）：

```bash
WATCHDOG_ENABLED=true
WATCHDOG_INTERVAL_SECONDS=60      # 巡检间隔（默认 60s）
WATCHDOG_STALE_AFTER_SECONDS=300  # 事件断流判定阈值（默认 300s）
WATCHDOG_MAX_RUN_SECONDS=3600     # 运行时长上限（默认 3600s）
```

**边界**：默认**只告警不处置**——收到告警后是否人工介入由你决定；要自动处置可实现 `WatchdogActionHandler` 插入（内置 `CancelHandler` 会对告警 Run 执行取消，可直接注入或作为参考实现）。同一 Run 同一原因只告警一次，不刷屏；刚创建的 Run 有宽限期，不会因为还没来得及发事件就误报。
