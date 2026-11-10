# 运行观测

> 本页讲每次 Run 的完整 Trace 记录了什么、为什么它是治理主线的数据地基、怎么查怎么看。

**是什么**：每次 Run 落一条完整 Trace：Agent 定义版本、实际模型、最终 Prompt、每一次工具调用（入参/出参/耗时）、Token 用量、费用、结束原因（完成/超步/超预算/取消/错误）。

**为什么需要**：排障靠它，评测素材靠它（[评测体系](./evals.md)必须基于真实运行观测），成本归因靠它（见[成本治理](./cost.md)）。一个 Trace 三种用途，是治理主线的数据地基。

**怎么用**：API 按会话/按用户标签/按时间查 Trace（`user_id` 是 Run 创建时透传的观测标签，见 [Agent Runtime](./runtime.md)）；[开发者工作台](./workbench.md)可视化查看，Trace 列表与详情直接显示每次 Run 的会话 ID 和用户标签；数据留在自己库里，无 SaaS 依赖。

## 平台日志与统一排查

**是什么**：`core/log_context` 会在请求、Run、子 Run、图/团队节点和工具链路之间传递 `request_id`、`session_id`、`run_id`、`trace_id`、`parent_run_id`、`root_run_id` 和 `subject`。符合条件的程序日志写入 `application_logs`，连同 Run 输入输出和 Trace 事件一起进入 `/diagnostics` 页面。

**为什么需要**：一个生产问题通常同时表现为“用户看到了什么”“引擎执行了什么”“代码报了什么”。统一关联键把三类现场拼到同一个执行树下，不用手工在日志文件和数据库里猜关系。

**怎么用**：`GET /v1/logs` 支持关联 ID、级别、logger、关键词、时间、分页过滤；`GET /v1/diagnostics/runs` 支持会话、Run、Trace、用户标签和状态过滤；`GET /v1/diagnostics/runs/{run_or_trace_id}` 返回根 Run、后代 Run、Trace 事件和日志数量。前端 `/diagnostics` 页复用同一批接口。HTTP 响应带 `X-Request-ID`，错误响应也能用它反查代码日志；`request_id` 只参与日志过滤，不是 Run 表的持久化字段。

**边界**：默认队列 10000、后台每 0.25 秒刷新一次，每次刷新取队列快照连续写多批、每批最多 200 条；单条正文/异常 UTF-8 上限 32 KiB，extras 序列化上限 16 KiB（超限记 truncated 标记与 4 KiB 预览）；服务关闭时先停止接收、等待写入中的批次提交，再 flush 剩余队列。保留期默认 30 天；队列满或写入失败不阻断业务，但整批可能丢失，通过查询接口暴露：`dropped_count` 是累计丢失日志条数（队列满、批次写失败/取消等所有原因相加），`write_failures` 是写入/清理失败事件的次数（非条数）。入站凭证会尽力脱敏，这不是任意个人敏感信息（PII）的识别保证；也没有自动捕获所有 `print` 输出或第三方库日志。

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
