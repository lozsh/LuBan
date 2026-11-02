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

# watchdog 模块

## 定位

`lubanagent/watchdog/` 是 Run 的**外部**看门狗控制回路：进程内 BudgetGuard（`lubanagent/runtime/budget_guard.py`）只在主循环活着的检查点生效，抓不住"主循环自己死了"的场景——进程重启遗留的僵尸 RUNNING Run、事件流断流、死锁。Watchdog 以进程外周期巡检补位：判 RUNNING Run 是否有实质进展信号，异常时告警并可插拔处置（默认只告警，可注入自动取消）（来源：`lubanagent/watchdog/service.py:1-11` 模块 docstring）。**默认关闭**（`Settings.watchdog_enabled=False`，env `WATCHDOG_ENABLED`，`lubanagent/config/settings.py:57`），由装配根 lifespan 在启用时启动巡检任务（`lubanagent/app/factory.py:168-188`）。模块刻意保持"只读 persistence + 发事件"的最小依赖面：判据只用现成仓储接口（`RunRepository.list_runs` + `TraceRepository.list_events`），不 import `runtime`/`api`，取消处置经注入回调绑定（import-linter 分层契约，`importlinter.toml:236-245`）。

## 接口契约

### HTTP

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/v1/watchdog/alerts` | 最近告警留痕（进程内 deque，重启清零）；`WATCHDOG_ENABLED=false` 或服务未装配时恒为空列表（来源：`lubanagent/api/routes/watchdog.py:10-16`） | 无错误路径（未启用返回 `[]` 而非 404） |

路由在 `lubanagent/api/routes/__init__.py:19、32` 挂载（`/v1` 前缀，路由聚合见 api-app-cli 模块文档）；路由只读 `request.app.state.watchdog`（鸭子类型，api 不 import watchdog 之外的框架内部）。

### RPC / MQ / Cron

无 MQ/Cron 中间件；巡检循环是 lifespan 内的 `asyncio.create_task(run_forever())`（进程内定时任务，非外部调度）。

### Python API

| 类/函数 | 签名 | 说明 |
|---|---|---|
| `WatchdogAlert`（frozen dataclass，`service.py:35-44`） | `alert_id: str`、`run_id: str`、`reason: Literal["stale_events","long_running"]`、`last_event_at: datetime \| None`、`age_seconds: float`、`created_at: datetime` | 一次告警；`alert_id` 为 uuid4 hex（注入 clock 的确定性场景下仍唯一） |
| `WatchdogService(*, db, settings, action_handler=None, clock=None)`（`service.py:80-99`） | `async check_once() -> list[WatchdogAlert]`（单次巡检，返回本轮新告警，可脱离循环直接调用——测试给固定时钟即确定可复算）；`async run_forever()`（巡检循环，异常只记日志不退出——外部控制回路自己不能先死，`service.py:149-157`）；`async aclose()`（置停止标志，循环下一轮退出）；`recent_alerts() -> list[WatchdogAlert]`（内存留痕，供 HTTP 查询） | `clock` 可注入（缺省 `datetime.now(UTC)`）是确定性测试前提；内部自建 `TraceWriter(db)` 发告警事件（`service.py:95`） |
| `WatchdogActionHandler`（Protocol，`service.py:47-52`） | `async def handle(self, alert: WatchdogAlert) -> str` | 告警处置协议；返回的动作名留痕进 `WATCHDOG_ALERT` 事件 payload 的 `action` 字段 |
| `AlertOnlyHandler`（`service.py:55-59`） | `handle` 恒返回 `"alert"` | 默认处置：只告警不动 Run |
| `CancelHandler(cancel_fn: Callable[[str], Awaitable[bool]])`（`service.py:62-77`） | `handle` 调 `cancel_fn(alert.run_id)`，成功返回 `"cancel"`、异常返回 `"cancel_failed"` | 内置可选处置：对告警 Run 执行取消；`cancel_fn` 由装配根绑定 `runtime.cancel`——**经注入回调而非 import runtime**（watchdog 包不依赖运行时，importlinter 契约） |

### 事件契约

告警落 `TraceEventType.WATCHDOG_ALERT`（枚举值 `"watchdog_alert"`，`lubanagent/core/trace.py:32`）trace 事件，**挂在目标 Run 的 trace 上**（`run_id=目标 Run`），payload：`reason` / `age_seconds` / `last_event_at`（ISO 8601 或 null）/ `action`（处置动作名）/ `alert_id`（`service.py:173-187`）。SSE 白名单映射为 `watchdog.alert` 外发（`lubanagent/runtime/sse.py:26`）——Watchdog 巡检不在 Run 事件流内，SSE 消费方只能在订阅该 Run 流时看到告警帧。

## 数据模型

本模块无专属数据库表。告警有两级留痕，均为非持久化内存态：

### 内存留痕（进程内）

| 结构 | 位置 | 说明 |
|---|---|---|
| `deque[WatchdogAlert]`（maxlen=100） | `service.py:32、96、172` | 最近 100 条告警，`GET /v1/watchdog/alerts` 的数据源；进程重启清零 |
| `dict[run_id, set[reason]]`（防抖表） | `service.py:97-98、111、134、144` | 同 run 同 reason 只告警一次；发现该 Run 已非 RUNNING 即清记录（之后再次 stale 可重新告警） |

### Trace 事件（持久化）

`WATCHDOG_ALERT` 事件经 `TraceWriter.emit` 落 `trace_events` 表（目标 Run 名下），是告警唯一的持久化留痕；事件落库失败不吞告警——内存留痕已记、日志兜底（僵尸 trace 场景也要能告出来，`service.py:188-192`）。

## 行为

### 状态机

无（模块无自身状态机；告警防抖是"每 (run_id, reason) 至多一次在告"的集合语义，非状态流转）。

### 调用树

#### check_once（单次巡检，判据全部来自现成仓储读接口）

```
WatchdogService.check_once()  # service.py:101-147
├── now = clock()  # 可注入确定性时钟
├── grace_seconds = watchdog_interval_seconds * 2  # 新 Run 宽限期：正常 Run 未必每 interval 内都有新事件
├── runs = RunRepository.list_runs(limit=200)  # 判据只用现成接口，不写专用查询
└── for run in runs:
    ├── [status != RUNNING] 清防抖记录，跳过  # 终态 Run 不巡检；此前告过警的 (run_id, reason) 记录同步清除
    ├── [started_at is None] 跳过
    ├── [run_age < grace] 跳过  # 宽限期内不告警
    ├── events = TraceRepository.list_events(run.id)
    ├── stale_events 判据：
    │   ├── [事件流非空] 末条事件距今 > watchdog_stale_after_seconds -> 告警
    │   └── [事件流为空] started_at 距今 > watchdog_stale_after_seconds -> 告警
    ├── long_running 判据：started_at 距今 > watchdog_max_run_seconds -> 告警（与 stale 独立，可同轮双告警）
    └── for reason in reasons:
        ├── [reason 已在防抖表] 跳过  # 同 run 同 reason 只告警一次
        └── 构造 WatchdogAlert -> _emit_alert
            ├── action = await action_handler.handle(alert)  # 处置器异常收敛为 "handler_error"，不阻断留痕（service.py:166-171）
            ├── recent deque.append(alert)  # 内存留痕
            └── TraceWriter.emit(WATCHDOG_ALERT 事件，run_id=目标 Run)  # 落库失败只记日志（service.py:166-192）
```

#### 装配与生命周期（lifespan，`lubanagent/app/factory.py:168-188`）

```
create_app lifespan
├── [settings.watchdog_enabled（默认 False）]
│   ├── watchdog = WatchdogService(db=db, settings=settings, action_handler=CancelHandler(runtime.cancel))
│   ├── app.state.watchdog = watchdog
│   └── asyncio.create_task(watchdog.run_forever())  # 每 watchdog_interval_seconds 巡检一轮
└── [shutdown] watchdog_task.cancel() + await 收敛
```

## 边界规则

- **默认关闭，存量部署零行为变化**：`WATCHDOG_ENABLED=false`（默认）时不创建服务、不起巡检任务、`/v1/watchdog/alerts` 恒为空（来源：`lubanagent/config/settings.py:57`、`lubanagent/app/factory.py:169-170`、`lubanagent/api/routes/watchdog.py:13-15`）。四个阈值参数 `watchdog_interval_seconds=60` / `watchdog_stale_after_seconds=300` / `watchdog_max_run_seconds=3600` 仅在 enabled 时生效（`settings.py:58-60`）。
- **判据不碰 BudgetGuard 的超时语义**：`boundary.timeout_seconds` 是 BudgetGuard 在主循环检查点的职责；Watchdog 只抓主循环自身失效（进程重启僵尸 RUNNING、事件流断流、死锁）——这些场景 guard 已不存在（来源：`lubanagent/watchdog/service.py:5-7` docstring）。
- **宽限期 = interval × 2**：新 Run 的末条事件/启动时间在宽限期内不触发告警，避免对正常执行节奏误报（来源：`service.py:104、117-118`）。
- **防抖与复位**：同 run 同 reason 至多告警一次（`_alerted` 集合）；该 Run 转为非 RUNNING（终态/取消）时清记录——之后若再次进入 RUNNING 且再次 stale 可重新告警（来源：`service.py:97-98、111、134-135、144`）。
- **处置器故障不阻断告警**：`action_handler.handle` 抛异常收敛为 `action="handler_error"` 继续留痕；告警事件落库失败同样只记日志不吞告警（内存 deque 已记）（来源：`service.py:166-171、188-192`）。
- **取消处置经注入回调，不 import runtime**：`CancelHandler` 持有 `cancel_fn` 由装配根绑定 `AgentRuntime.cancel`；import-linter 禁止 `lubanagent.watchdog` import `lubanagent.runtime`/`lubanagent.api`/`fastapi` 等（只依赖 persistence + config + core + observability）（来源：`service.py:62-77`、`importlinter.toml:236-245`）。
- **留痕均为进程内存 + 目标 Run trace，无独立持久化**：`recent_alerts` 重启清零（HTTP 查询是进程内视图）；跨进程审计走目标 Run 的 `WATCHDOG_ALERT` trace 事件（来源：`service.py:96、163-164、173-187`）。
- **巡检覆盖上限**：每轮 `list_runs(limit=200)`，超过 200 个 Run 的部署中更早的 Run 不在本轮巡检范围（现成仓储接口的既有分页语义，未为此新增查询）（来源：`service.py:107`）。
- **告警帧仅在订阅目标 Run 流时可见**：SSE 消费方订阅的是具体 Run 的事件流，Watchdog 对僵尸 Run 发的告警帧只有该 Run 的流被订阅时才会经 `watchdog.alert` 外发；运维主入口是 `GET /v1/watchdog/alerts` 与 trace 查询（来源：`lubanagent/runtime/sse.py:26`、`lubanagent/api/routes/watchdog.py:10-16`）。

## 名词

| 术语 | 语义 |
|---|---|
| stale_events | 告警原因之一：RUNNING Run 的末条 trace 事件距今超过 `watchdog_stale_after_seconds`（事件流为空时按 `started_at` 计）——Run 疑似断流/死锁/进程重启遗留（`service.py:119-130`） |
| long_running | 告警原因之二：RUNNING Run 的 `started_at` 距今超过 `watchdog_max_run_seconds`——超长运行，与 stale 独立判定、可同时告警（`service.py:131-132`） |
| WatchdogAlert | 单次告警记录（alert_id/run_id/reason/last_event_at/age_seconds/created_at），内存 deque 与 WATCHDOG_ALERT 事件的共同载体（`service.py:35-44`） |
| grace（宽限期） | `watchdog_interval_seconds × 2`：Run 启动后该时长内不告警，容忍正常执行节奏下的事件间隔（`service.py:104`） |
| 外部控制回路 | 本模块的定位用语：主循环内的治理（BudgetGuard 三闸、协作式取消）随主循环死亡而失效，Watchdog 作为进程外周期巡检补位，处置动作经注入回调回到 runtime（`service.py:1-11`） |

## 上下游

**上游依赖**（本模块消费外部数据/能力）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.persistence` | `Database.session()` + `RunRepository.list_runs` / `TraceRepository.list_events` | 巡检判据的全部输入（RUNNING Run 清单 + 各 Run 事件流末条时间）（`service.py:26-28、106-108、122`） |
| `lubanagent.observability` | `TraceWriter.emit` | `WATCHDOG_ALERT` 事件落库（目标 Run 的 trace 行）（`service.py:25、95、173-189`） |
| `lubanagent.config` | `Settings` | `watchdog_enabled` 与三个阈值参数（`service.py:23、92`；`lubanagent/config/settings.py:57-60`） |
| `lubanagent.core` | `RunStatus`/`TraceEvent`/`TraceEventType` | RUNNING 状态判定与告警事件模型（`service.py:24`） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent.runtime`（AgentRuntime.cancel） | 经 `CancelHandler` 注入的 `cancel_fn` 回调 | `long_running`/`stale_events` 告警的自动处置：取消僵尸 Run（装配根绑定，`lubanagent/app/factory.py:171-175`；watchdog 不 import runtime） |
| `lubanagent.api`（`api/routes/watchdog.py`） | `request.app.state.watchdog.recent_alerts()` | `GET /v1/watchdog/alerts` 查询进程内告警留痕（`routes/watchdog.py:13-16`） |
| 目标 Run 的 trace / SSE 消费方 | `WATCHDOG_ALERT` 事件（SSE 名 `watchdog.alert`） | 告警持久化留痕与实时外发（订阅该 Run 流时可见）（`lubanagent/core/trace.py:32`、`lubanagent/runtime/sse.py:26`） |
