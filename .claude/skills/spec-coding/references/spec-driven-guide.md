# 正向契约提取与编码指南

本指南覆盖正向路径（意图/spec → 契约 → code）的契约提取、编码对照、门槛检查。**语言无关**：工具链命令按目标仓库实际语言选择。

## 通用原则

- **契约是本次任务的 source of truth**（载体随宿主：任务目录 `contract.md` 或 plan 文件）：契约从 spec/意图 + 现有 `docs/specs/modules/<module>.md`（若存在）提取，代码照契约实现。
- **契约必须具体、可验证**：抽象到"实现用户功能"无效，必须落到签名/字段/步骤/验收标准。
- **边界硬约束代码强制**：钉死的权限/超时/预算/依赖方向用代码实现，不靠注释或 Prompt 声明。
- **只做契约范围内事**：不越界实现范围外功能；发现缺口走契约演进协议，不擅自扩。
- **契约螺旋演进**：编码中发现契约缺口是常态。技术性细节（不改用户可感知行为：参数默认值、错误码选择、字段命名、实现次序）→ 主 agent 自主决策 + 继续编码 + 契约补记（决策+理由）；方向性选择（改用户可感知行为、影响范围边界、不可逆）→ 问用户，答案写进契约。判定口诀：用户半年后回看会不会有意见？会→问，不会→自主定+补记。编码结束进验证前，把全部演进决策补记完整。

## 工具链探测

```bash
ls pyproject.toml setup.py requirements.txt 2>/dev/null   # Python
ls package.json pnpm-lock.yaml yarn.lock 2>/dev/null        # Node
ls pom.xml build.gradle build.gradle.kts 2>/dev/null        # JVM
ls go.mod 2>/dev/null                                        # Go
ls Cargo.toml 2>/dev/null                                    # Rust
```

后续门槛检查命令按探测结果选择。

---

## 契约提取（每个任务执行一次）

对当前任务，从 spec/意图 + 现有 `docs/specs/modules/<module>.md`（若存在，作为现状基线）提取以下六类，写入任务目录 `.spec-coding/changes/<change-id>/contract.md`（本次任务的过程产物，PASS 后经 §F6 判断决定是否同步进 `docs/specs/`，不直接等同于长期资产）：

### 1. 接口契约

从 spec 的 API/接口设计章节提取：

- 函数/类/API 端点的**完整签名**：名称、参数（名+类型）、返回类型。
- 每个参数的语义与约束（必填/可选、取值范围、格式）。
- 返回值结构（字段+类型）与错误情况（异常/错误码/状态码）。
- 示例（spec 给的 curl / 请求体 / 响应体原样抄入）。

```
接口：POST /v1/agents/{name}/runs
入参：name(str, 路径), input(str, body), stream(bool, 默认 true)
返回：SSE 事件流（run.started / token.delta / tool.started / tool.finished / run.completed）
错误：404 agent 不存在；429 超预算；408 超时
来源：技术方案 §3 / 用户文档 §3
```

### 2. 数据契约

从 spec 的数据模型/ORM/schema 章节提取：

- 实体/表/集合名、字段名、字段类型、约束（非空/唯一/默认/外键）。
- 索引（来源 spec 明确的）。
- 枚举值（全部列出，逐字）。

```
表：agent_definitions
字段：id(str,主键), name(str,唯一), brain(enum[fast,smart,cheap,vision]), version(int,默认1), instruction(text)
枚举 brain：fast / smart / cheap / vision（来源：架构设计 §逻辑大脑）
来源：技术方案 §数据模型
```

### 3. 行为契约

从 spec 的流程/状态机章节提取：

- 核心流程步骤（按顺序，确定性步骤标"代码强制"，模型决策步骤标"模型判断"）。
- 状态转换：状态节点 + 触发条件 + 触发接口。
- 前置条件 / 后置条件。

```
行为：Agent Run 循环
步骤：
  1. 加载 agent 定义版本（代码强制）
  2. 组装上下文（代码强制，遵守 KV-cache 不变量）
  3. 模型推理 → 决定调工具 or 给答复（模型判断）
  4. 执行工具（代码强制，权限检查+超时+重试）
  5. 结果回填 → 回到 3
  6. 触碰边界（max_steps/budget/timeout）→ 终止（代码强制）
前置：agent 定义存在且为发布版本
后置：落一条完整 Trace
来源：用户文档 §3 + 架构设计 §Runtime
```

### 4. 边界契约（硬约束，代码必须遵守）

从 spec 的"不可动摇""硬约束""边界""不做"类章节提取，逐条列出：

- 依赖方向约束（如 `app/* → lubanagent/*` 允许、反向禁止）。
- 权限/审批/金额等确定性规则。
- 超时/预算/步数上限。
- "不做"清单（明确不实现什么）。

```
边界：
  - 依赖方向：app/* → lubanagent/* 允许；lubanagent/* → app/* 禁止（import-linter 守卫）
  - 预算：Run 级 budget_tokens 撞线即停，写 Trace
  - 不做：在线托管 SaaS、低代码设计器、通用后台管理
来源：AGENTS.md §2/§4
```

### 5. 验收标准

从 spec 的验收标准/测试用例章节提取，**必须可实跑验证**：

- 测试用例（输入 + 期望输出/期望工具调用）。
- curl 命令 + 期望响应。
- 类型检查 / lint 通过。
- 性能阈值（若有）。

```
验收：
  - 单测：test_agent_runtime.py::test_budget_exceeds_stops（输入超预算 → run 终止，结束原因=over_budget）
  - curl：POST /v1/agents/cs/runs -d '{"input":"退款"}' → SSE 含 run.started + run.completed
  - 类型检查：mypy lubanagent/ 无错
  - 边界：import-linter 证明 lubanagent/* 不 import app/*
来源：开发计划 D1-4 验收标准
```

### 6. 存疑项决策清单

不明确处逐条列出，用 AskUserQuestion 询问，决策回填 `contract.md`：

```
存疑项：
  1. spec 未说明 Run 取消后已执行工具是否回滚
     → 用户决策：不回滚（已执行副作用保留，Trace 记录取消点）
     来源：用户决策 2026-07-22
```

---

## 契约质量基线

- 接口契约：每个签名有完整参数类型与返回结构；错误情况列全。
- 数据契约：字段类型与约束明确；枚举全列。
- 行为契约：确定性步骤标"代码强制"，模型步骤标"模型判断"。
- 边界契约：spec 硬约束逐条落地，每条带来源。
- 验收标准：每条可实跑，不是"功能正常"这类空泛描述。
- 存疑项：每条有用户决策，不悬空。

**不合格契约**（须补全再编码）：接口签名缺类型；验收标准不可实跑；边界约束只写"注意安全"无具体规则；存疑项未决策。契约质量基线是**任务目录 contract.md 的门槛**，不是资产目录模块文件的门槛——模块文件的质量基线见 `code-to-docs-guide.md`。

---

## 编码对照规则

实现时逐条对照契约：

1. **签名对齐**：函数/类/API 名称、参数名与类型、返回类型与契约逐字一致。需偏离 → 按「契约螺旋演进」分流：技术性细节自主定 + 契约补记，方向性选择问用户。
2. **数据对齐**：ORM/模型字段名、类型、约束与数据契约一致；枚举值全列不漏。
3. **行为对齐**：确定性步骤用代码强制（if/断言/状态机），不靠 Prompt/注释声明；模型判断步骤交给模型。
4. **边界对齐**：每条边界契约对应一处代码强制点（依赖守卫、预算检查、权限校验等），可被测试覆盖。
5. **范围对齐**：只实现本次任务契约内功能。范围外功能不提前实现；发现缺口记入存疑项问用户，不擅自扩。
6. **风格对齐**：遵守仓库既有代码风格（命名、分层、注释密度），读周边代码再下笔。

## 门槛检查工具链（按语言）

正向门槛 = 类型检查 + lint + 单元测试通过。`verify.sh --mode forward` 探测并跑：

```bash
# Python
python -m mypy <pkg> 2>/dev/null || true      # 类型检查（有则跑）
python -m ruff check . 2>/dev/null || true     # lint（有则跑）
python -m pytest -q 2>/dev/null                # 测试

# Node
npx tsc --noEmit 2>/dev/null || true           # 类型检查
npm run lint 2>/dev/null || true               # lint
npm test 2>/dev/null                           # 测试

# JVM
./mvnw test -q 2>/dev/null || ./gradlew test -q 2>/dev/null

# Go
go vet ./... 2>/dev/null || true
go test ./... 2>/dev/null
```

工具不存在则跳过该项并在 `tasks.md` 注明；**测试必须实跑**（验收标准要求），不可跳过。`verify.sh` 只做最低门槛，不能代替 strict-validation subagent。

## worker subagent 拆分（大任务）

适合拆 worker：不同文件的独立实现（如一个 worker 写 `model/factory.py`，另一个写 `runtime/agent.py`）；单模块事实取证。

不适合拆 worker：契约对齐与集成、通过/失败判定、多 worker 同改一文件、输出 Recommended Verdict。

派发 worker 至少给出：负责的文件/模块路径、契约切片（它实现的接口/数据/行为）、允许读取的 `contract.md` 与代码范围、是否有写权限及可写范围、完成后须回报（已实现契约项/已核对来源/未解决缺口）。

主 agent 收到 worker 结果 → 先更新 `tasks.md` → 负责契约对齐与集成 → 再决定继续拆分/进审稿。**禁止 worker 自己派发 reviewer 或在 worker 线程切到审稿。**
