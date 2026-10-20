# AgentHarness v2（pi 项目）多维度分析

> 这是什么：对 earendil-works/pi 仓库 `harness-v2.md`（3446 行技术方案）的多维度深度分析。
> 更新日期：2026-09-07。原文不随本仓库分发，取自 github.com/earendil-works/pi 分支 harness-v2 路径 `packages/agent/docs/harness-v2.md`（§号引用均以它为准）。
> 用途：一层是学它的**内容**（怎么设计可崩溃恢复的 Agent 运行时），一层是学它的**方法**（怎么写一份 AI 能直接执行的技术方案）。

## 0. 一句话定位

这是一份「持久化 Agent 运行时」（durable agent harness）的设计规格：让 Agent 的每一次运行（run）成为**持久化操作**——进程崩了、重启后从最后的安全边界继续，任何崩溃点都不产生"半完成"状态。同时它本身是一份教科书级的 **spec-as-contract** 范本：整份文档以不变量 + 伪代码 + crash 矩阵写成，供多个 AI agent 按工作包并行实现。

### 心法速记（五条，按优先级）

1. **崩溃是数据结构问题，不是运维问题**——记录写得对，崩溃就只是"换个进程重放"。
2. **先写意图，再做副作用**（intent-before-effect）：副作用前先持久化"将要发生什么 + 将产生什么 id"；意图满足 ⇔ 该 id 的条目存在。不需要跨记录原子性。
3. **状态 = 记录的规约**：运行时内存态和崩溃恢复态用同一套规则推导，就永远不可能不一致；live 与 resume 是同一个过程。
4. **不变量必须可判定**：每条规则都要能写成"给定存储，真或假"的断言；形容词（"可靠的""一致的"）不算设计。
5. **竞态列清单逐个证明，不靠小心**：枚举全部竞态，每行写明合法历史只有哪几种、由什么机制强制，两种顺序各测一次。

## 1. 内容维度：它解决了什么问题

### 1.1 问题定义（§1 Goals）

| 目标 | 含义 |
|---|---|
| Durable runs | 接受的 prompt 是持久操作；崩溃后新进程恢复会话，从最后安全边界续跑 |
| Lanes | 一个会话内多条并行工作线（Slack thread = lane，subagent = 父会话的新 lane） |
| No partial outcomes | 任何操作崩溃后只有两种状态：没发生 / 恢复可完成，无中间态可观察 |
| Deterministic stepping | 每个副作用过同一个注入边界，测试可以逐步驱动、任意点模拟崩溃 |
| Single writer | 一个会话同一时刻只有一个写者，服务层强制 |

**关键判断**：它把"Agent 跑着跑着崩了怎么办"从运维问题变成了**数据结构问题**——只要记录写得对，崩溃就只是"换个进程重放"。

### 1.2 状态模型：Session 四态（§2）

```
1. tree          会话树：只追加的条目（消息/模型切换/压缩摘要），共享、被动
2. lanes         命名工作位置：name + leaf，主动，每 lane 最多一个开放操作
3. op logs       每 lane 一条扁平操作日志：持久化的真正实现处
4. global facts  会话级最新值（名字、标签），不属于树
```

**最有价值的切分：主动/被动分离**。树和事实是纯数据，任何东西可读；lane 是活动的（拥有 leaf、操作日志、队列、待写）。类比对：lane ≈ git branch checked out in its own worktree——名字挂在位置上、追加推进、可跳到任意条目、绝不双重检出。

**不变量举例**（每条都可判定为真/假）：
- 树只存对话；任何指针不进树。
- 删除全部操作日志，剩下的仍是一个完整合法的会话。（→ 日志描述执行，不描述对话）
- 一个 lane 两个开放操作 = 损坏。

### 1.3 持久化协议：intent-before-effect（§5）

> **耐久性规则：副作用之前，先写一条意图记录，命名将要发生的事和它将产生的 id；副作用之后，以完全相同的 id 追加结果条目。**

- **没有多记录原子性，也不需要**。每条记录、每个条目各自独立持久。崩溃夹在意图和结果之间 → 恢复按意图类型决定：补完 / 重试 / 用合成结果关闭。
- **Provisioned id**：意图记录携带"尚不存在"的条目 id。意图满足 ⇔ 该 id 的条目存在。同 id 不同内容 = 损坏。这一个判定规则就把"这件事做没做完"变成了 O(1) 点查。
- 记录目录只有 9 种（operation_started / abort_requested / operation_finished / step_attempt / tool_started / queue_enqueued / queue_cancelled / write_deferred / usage）——**小到可以穷举验证**。

### 1.4 恢复模型：状态 = 记录的规约（§7）

- 恢复不用全量扫描：先索引查开放操作（0=idle，1=suspended，2=损坏），再做两次**有界读取**（该操作的记录 + 该 lane 自己的条目）。所有扫描被开放操作或待处理队列界定，与总会话历史无关。
- **Live 与 restore 跑同一套规则**：运行时在内存里随写更新状态，恢复时从存储重算状态——状态和记录不可能不一致，因为**状态就是它们的规约（reduction）**。
- 恢复期间的追加就是普通追加 + 一条规则：跳过已存在的 provisioned id。→ 恢复本身崩了也安全，重跑恢复即可。
- **定点自检**：每次 resume 结束后从存储重算规约、与内存 LaneState 比对，不一致 = 损坏 = fault。写者/规约漂移当场抓住，而不是等下一次崩溃。

### 1.5 并发模型：mutation line + race catalog（§15）

- 所有竞态只有一个形状：**从 lane 状态做决策 → await → 用过期决策提交写入**。解法是结构性的：每 lane 一条 FIFO promise 链（mutation line），每个"依赖状态的决策"在链上一个 job 里完成（校验 → 最多一次耐久写 → 更新内存态）。provider 请求/工具/hook/退避**绝不**进 job。
- 两个并发操作因此只有两种合法历史（[A,B] 或 [B,A]），不存在第三种交错。
- **Race catalog：12 行竞态全枚举表**（§15），每行写明两种合法历史和强制它的机制，Tier C 测试对每行两种顺序各测一次。竞态不是"小心避免"，是**列清单逐个证明**。

### 1.6 成本模型：账本与结果解耦（§5）

> **成本耐久不得依赖结果耐久。**

- 可重试步骤的设计本意就是产出"永远不会成为条目"的响应（失败尝试、被丢弃的溢出响应），它们的花费不能随之消失。
- 因此**每个 provider 请求一结算就写 usage 记录**，先于任何分类/重试/丢弃。
- 三层分离：条目上的 `usage` 字段 = 不可变快照；条目的有效成本 = 读时按 entryId 汇总账本；会话成本 = 全账本之和。恢复可以诚实地计费两次（重试/重放各记一次）。

### 1.7 上下文经济：append-only context（§4/§6）

> 跨请求，provider 上下文只在尾部增长。尾部之前插入任何东西，都会使 KV cache 从该点失效，token 成倍烧钱。

- 这是"回合中写入延迟到 checkpoint 应用"的根因——checkpoint 应用恰好是尾部追加。压缩是唯一故意的例外：用一次全量 cache 失效换更小的上下文。
- **溢出分类算法**（§6）：`length` 停止有歧义（真到输出上限 vs 上下文压力）。判据是"实际输出（含 reasoning）是否达到**预期**输出上限"，预期值取调用方 maxTokens 或模型 maxTokens（clamp 之前），绝不取实际发送值。可恢复响应**直接丢弃**（不成为条目，重试无需清洗上下文），成本已入账本。
- **每次对话输入只允许一次溢出恢复**：防止 compact-and-retry 死循环；只有新的对话输入（prompt/steer/followUp）能重置守卫。

### 1.8 可观测三通道（§10/§11/§18）

| 通道 | 能否改变执行 | 持久化 | 用途 |
|---|---|---|---|
| Events | 不能（被动） | 否，不重放 | UI/观察：快照 + 活事件流 |
| Hooks | 能（拦截） | 输出随记录持久 | 扩展：改上下文/请求/工具/边界 |
| Telemetry | 不能 | 否，进程本地 | 诊断：span 树，与 hook 系统分离 |

- 事件承诺：passive（抛错不影响执行）、有序、报最终值（hook 变换后）、JSON 可序列化无秘密。
- `watch()` 的快照+缓冲原语：捕获快照和开始缓冲是**一步**，`start()` 先按序冲缓冲再转 live——无序列号、无注册竞态、每事件恰好一次。
- Hook 重放表（§11）：每个 hook 在 fresh/retry/resume 三列下的行为一张表说死。原则：**hook 只在其工作本身重跑的地方重跑；已持久化的输出绝不重算**。`before_tool` 抛错 = fail closed（阻塞工具）是唯一例外。

### 1.9 API 设计：Result + TaggedError（§8）

- 操作方法**永不抛异常**：每次调用返回 `Result<T, E>`。Promise rejection 只意味着 defect 或死掉的 harness。
- 每种预期拒绝是一个带字面量 `_tag` 的类；加一个类 → 错误 union 变 → 所有 exhaustive `matchError` 编译失败。**用类型系统逼着调用方处理新错误**。
- 所有方法 async（包括内存实现能同步回答的 getter）：接口必须可被远程 proxy 实现，不能承诺只有本地实现能守的同步性。
- 全 async 但例外明确（name、监听注册）——例外也有理由（server 自己桥接事件传输）。

### 1.10 测试策略：三层 + 机械崩溃矩阵（§19）

| 层 | 测什么 | 怎么测 |
|---|---|---|
| Tier A | 规约与恢复 | 公共 Session API 预填一个 §6 崩溃前缀的记录/条目，开 harness，resume，断言耐久结果 |
| Tier B | 写者一致性 | 真跑 harness，插桩记录每次 E/R/L/G/H，**逐条断言与 §6 trace 完全一致的顺序** |
| Tier C | 确定性交错 | `drive: "manual"`：真 harness + 假 provider + 真后端，gate 是唯一测试钩子 |

三个机制值得偷走：
1. **Manual drive（确定性步进）**：每个副作用过同一个注入 `Effects` 边界；manual 模式下每个调用先泊车，测试 `peekAction()/executeAction()` 逐个驱动。生产和测试跑**同一套过程**，drive 模式只控制边界。崩溃模拟 = 在选定边界 `close()` + 重开后端 + `resume()`。
2. **机械生成崩溃矩阵**：crash 位点不是手挑的——manual 模式跑每个 trace，**每个 executeAction() 之后都快照后端**，然后每个快照都重开 + resume（且每个跑两遍，证明半完成的恢复是安全的）。新增副作用自动获得崩溃覆盖。
3. **Tier B 可执行不变量**：append-only context 不变量写成测试——run 内每个请求的消息列表必须是前一请求的精确前缀（压缩条目除外）。散文原则变成会失败的测试。

## 2. 方法维度：这份方案本身怎么写的

这部分和内容同样值钱——它是"写一份 AI 能直接执行的技术方案"的范本，也就是 vibe-coding 的高级形态。

1. **三段式结构**：Part I 概念（为什么/是什么）→ Part II 记录（backend 无关的耐久性语义）→ Part III API 与实现。每层只依赖前面的层，实现者可以分层验收。
2. **兼容性政策一句话钉死**（文档开头）：唯一向后兼容要求是 v3 会话能打开并恢复 idle；其余格式/API 随便 break、不写迁移。范围先于一切。
3. **不变量驱动**：每个概念节都带 invariants 小节，每条不变量可真伪判定、可写成测试断言。设计 = 概念 + 不变量，而不是概念 + 形容词。
4. **伪代码即规格**：§15 的 harness 内部直接给 TypeScript 伪代码（driverLoop / assistantStep / abortPath / reconcileToolBatch），且明确标注"这是规格而非实现细节"。§5 记录目录、§8 API 全部是完整类型定义。
5. **trace + crash 表**：§6 每个场景给 E/R/L/G/H 逐行 trace，再配"crash 在任何两行之间都可恢复"的位点表。恢复语义不是叙述，是查表。
6. **显式 Non-goals**（§1）：明确列出不做 exactly-once hook 副作用、不做 provider 流恢复、不做多写者、不做复制。每个 non-goal 还解释了为什么不需要/怎么绕。
7. **公共方法所有权表**（§20）：每个公共 API 方法 ↔ 拥有它的 work package 一一对应。没到归属包之前一律 `HarnessNotImplemented` 拒绝，**禁止假装成功**（F0 的验收标准就是"没有未完成的方法报告貌似合理的成功"）。
8. **Reservation 协议**：认领包 = 先单独提交一行 `**Reserved: X by @user**`，落到 main 才算认领；冲突时后到者撤。多 agent 并行开发的协调机制直接内建在文档里。
9. **测试与实现同包落地**：每个 work package 的验收标准里明确"测试编码验收标准 + 该包拥有的每条设计不变量；happy-path 覆盖不够"。
10. **设计即依赖图**：结尾给出合并顺序（F0+R3+I2+I5 收敛门 → H0…H8 → C/N → O），每条 lane 串行、lane 间并行，避免并发重写同一文件。

## 3. 可迁移清单（用到自己项目里）

### 对 LubanAgent 直接相关

| pi 概念 | LubanAgent 对应 | 动作 |
|---|---|---|
| Session 四态 + 主动/被动分离 | 当前会话/消息模型 | 评估：把"执行状态"从对话数据里剥离出去 |
| intent-before-effect + provisioned id | Run 持久化 | 引入意图记录模式，崩溃可恢复的 Run |
| 状态=记录的规约 + 定点自检 | 治理/Trace | "live 与恢复同一套规则"的自检思路可用于预算/步数治理 |
| usage ledger 与结果解耦 | 成本模块（D2） | 失败请求/重试的花费也要入账，且与条目解耦 |
| append-only context | 上下文构建 | 保证请求间只在尾部追加，保护 KV cache |
| 溢出分类（对比预期输出上限） | 压缩触发 | 比"百分比阈值"更正确的 length 歧义判据 |
| 三层测试 + manual drive | spec-coding 的验证闭环 | Tier B"trace 逐行断言"和"机械崩溃矩阵"是 strict-validation 的强化方向 |
| race catalog 全枚举 | 多 Agent 编排 | 编排 Run 的竞态同样可以列清单逐个证明 |

### 对写技术方案的通用方法论

写方案时的 checklist（详见本 skill SKILL.md「契约质量铁律」）：范围与非目标先行 → 概念+不变量 → 数据记录定死 → trace+crash 表 → 伪代码即规格 → 公共面所有权表 → 测试分层随包验收 → 依赖顺序收尾。

## 4. 批判与局限

- **复杂度成本高**：9 种记录、12 行竞态表、三层测试——为一个 Agent CLI 运行时付出数据库引擎级的规格成本。小项目照抄全套是过度设计；该学的是**按问题大小裁剪**（比如只取 intent-record + reduction 两件套）。
- **单写者 + 无复制**是硬边界：分布式多写、协同编辑不在模型内，作者明确不假装解决。
- **JSONL 耐久性是进程崩溃级**：resolved append 即耐久，无 fsync 承诺；断电场景留作显式能力。
- **强 TypeScript 特化**：类型即文档的做法依赖编译器生态；Python（LubanAgent）里要用 pydantic/测试去补位。
- **文档本身 3446 行**：能这样写是因为它同时充当多 agent 的实施合同。团队内部小功能用不着这个重量，但"不变量可判定、trace 表化、测试随包"的原则任意规模都成立。

## 5. 进一步阅读

- 原文：github.com/earendil-works/pi（branch harness-v2, path j4/packages/agent/docs/harness-v2.md），§号引用以它为准
- 相关概念溯源：WAL（write-ahead log）、event sourcing（状态=事件规约）、Redis transactional outbox（intent-before-effect 同族）、SQLite WAL 的 torn-tail 处理
