# LubanAgent 设计债与演进方向

> 状态：演进方向笔记（非定稿方案）。来源：2026-10 与 [prodagent](https://github.com/limenagent/prodagent)（~1800 行零依赖的教学级 Agent 内核）的对照分析。本文只钉"弱在哪、为什么弱、往哪演进"，不做排期。

## 0. 结论先行

LubanAgent 的工程成色（测试、领地隔离、定义版本化、治理边界）是真材实料，不是玩具。实质差距在**机制层的统一性**：我们采用"借 LangGraph 当引擎 + 在包装层外挂治理"的架构，导致执行状态、治理状态、事件记录三套事实互相分裂，编排原语不完备，新增编排模式必须改框架层。prodagent 用"EventLog 是唯一事实 + 模式=原语组合"一个思想同时消灭了这些问题。

对照维度（prodagent 术语 → 我们现状）：

| 关注点 | prodagent | LubanAgent 现状 |
|---|---|---|
| 事实源 | append-only EventLog，state = fold(events) | 三处分裂：LangGraph checkpoint（执行态）+ 内存 guard/token（治理态）+ TraceWriter 旁路双写（事件） |
| 编排原语 | go / send / 无返回边 handoff / wait_human | 条件边仅 then/otherwise；无动态扇出；无 handoff；多 Agent 是预制 supervisor 模板 |
| 新增编排模式 | 应用层组合原语，内核零改动 | 改 `lubanagent/multiagent/` 或 `graph/` 框架层 |
| 治理位置 | 长 Scheduler 波边界，天然统一 | 平行补丁（budget_guard / cancellation / recursion_limit），GraphRunner 节点边界手工串接 |
| 外部可见性 | Bus 内核属性（observe/adjudicate/subscribe） | astream_events 从外面捕事件，子 Run 泄漏靠 tool_run_ids 手工隔离 |

## 1. 设计债清单（代码事实）

### D1 事实三分：执行态 / 治理态 / 事件互不归属

- 执行状态在 LangGraph checkpoint；事件经 `astream_events(v2)` 从引擎外捕获，子 Run 事件泄入父流需 `tool_run_ids` 集合手工隔离（`runtime/agent_loop.py`，防止重复计费）。
- 治理状态（BudgetGuard、CancellationToken）是进程内对象、不进 checkpoint——`graph/runner.py` interrupt 后把 (guard, token) 冻结在内存绑定表，跨进程重启只能"降级为 runner 自身 budget 兜底（如实降级，不假恢复）"。
- Trace 是旁路：TraceWriter 双写 DB + Langfuse，与执行态无推导关系，无法从事件重建 Run 状态。

**为什么弱**：prodagent 中 wait_human 的问题与答案都是日志事实，resume 从事实重建，恢复免费且跨进程。我们为"恢复"打的每个补丁（内存绑定表、resume 串行锁、终态拒绝表）都是事实分裂的症状。

### D2 编排原语不完备，模式靠模板兜底

- `graph/builder.py` 自认"demo 级全集"：条件边只有 if/else（then/otherwise），无 `send()` 动态分发，无 handoff（无返回边）语义。
- fanout 在 `multiagent/team.py` 节点内 `asyncio.gather` 绕行（LangGraph 原生并行边无 reducer 会互相覆盖 state，且 builder 不支持 Send）。
- 多 Agent 是写死的 supervisor 拓扑；要新增 swarm / blackboard 等模式必须改框架层——恰是 prodagent "strategy 在应用层" 反面。

### D3 治理是平行补丁，不是推导结果

步数（recursion_limit）/ 预算（BudgetGuard）/ 超时 / 取消各自独立实现，靠 GraphRunner 在节点边界手工调 `check_cancelled()` / `check_budget()` 串接。本可以是同一"波边界检查"契约的推导物；新增治理项（如敏感词闸、速率限制）仍需碰执行循环。

### D4 确定性测试未升格为系统能力

FakeLLM 固定台词 + 离线测试的思路与 prodagent 的 scripted model 同源（这点做对了），但只停留在测试件：剧本不能驱动真实 Run 的端到端离线重放，evals 的 replay 模块与 FakeLLM 未打通成"一条剧本 = 一个可回归场景"。

## 2. 演进方向（按性价比排序）

### E1 EventLog 升格为事实源（消灭 D1）

把 TraceEvent 从旁路双写改为 append-only 主干：Run 的用量账本、状态机迁移、治理快照都从事件 fold 推导。最小切口：用量账本从事件聚合替代内存 BudgetGuard 累加——直接消灭 GraphRunner 的跨进程预算降级问题。checkpoint 仍归 LangGraph（不重造引擎），但"恢复后该剩多少预算"由事件事实回答。

### E2 补齐编排原语，多 Agent 降为示例策略（消灭 D2）

给 GraphBuilder 补 `send(target, state)`（LangGraph 原生支持，我们只是没暴露）与 handoff（无返回边）语义；条件边从 if/else 扩展为路由函数。supervisor 团队改写为"用原语拼出的一个应用层策略示例"，`multiagent/` 从模式仓库退位为组合示范。验收标准：新增一种编排模式不需要改 `lubanagent/` 领地。

### E3 治理收敛到统一波边界契约（消灭 D3）

把 `NodeHooks.check_cancelled/check_budget` 推广为统一的"节点边界检查链"协议，步数/预算/超时/取消/未来治理项都注册为检查链节点。执行循环不再认识具体治理项。

### E4 scripted model 升格为产品能力（消灭 D4）

FakeLLM + evals replay 打通：剧本（输入序列 + 模型台词 + 工具返回）驱动整条 Run 的离线确定性执行，评测集 = 剧本集，CI 回归 = 重放剧本。零密钥口径从"只验链路"升级为"可验任意业务流程"。

## 3. 不做什么

- **不重造引擎、不替换 LangGraph**。prodagent 是教学内核，它自己定位是"读懂它让你更好地回到 LangGraph"。我们吸收的是它的状态模型思想，不是代码。
- **不动已交付契约的对外语义**（Run API / SSE / 领地隔离 / 定义版本化）。E1-E4 全部是框架领地内部重构，对 `app/` 领地与用户 API 零破坏。
- **不把"机制纯度"当卖点写进定位**。产品主线仍是 Agent 起步基座，本文是内部工程演进，不是对外承诺。

## 4. 相关参考

- `LubanAgent-superagent-design-reference.md`（2026-11-02，DeepSeek Harness Super-agent 对照）：本文管**执行内核**的事实统一（EventLog 单一事实源、编排原语）；该文管**系统形态**演进（事件驱动、异步 ReAct Loop、外部 Watchdog）。两文互补，其 §2.1/§2.3 的落地应以本文 E1-E4 的事实统一为前置。
