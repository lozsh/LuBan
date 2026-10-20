# 写 Tool 与 Skill

> Tool 是"能做什么"（可执行的动作），Skill 是"什么时候做、按什么章法做"（知识与流程的包）。两者组合：Skill 正文指导模型何时调用哪些 Tool。

## Tool：业务动作唯一要写代码的地方

**是什么**：Tool 就是一段 Python 函数，加几样声明：自然语言描述（给模型看）、参数 JSON Schema（从函数签名 + type hints 自动生成，给模型填）、治理属性（风险、审批、超时/重试/幂等）。

**为什么**：确定性规则（权限、金额、审批）必须由代码执行，不能靠 Prompt 声明。Tool 支持独立调试与 Mock——不启动 Agent 也能单测一个 Tool。

**怎么写**：

```python
from lubanagent.extension import tool

@tool(description="查询订单状态与物流", risk="low", idempotent=True, timeout_seconds=10)
def query_order(order_id: str) -> dict: ...
```

放在 `app/tools/` 下即被扫描注册（import 即注册，见[改造总览](./overview.md)）。

### @tool 全参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `key` | 函数名 | 注册标识，Agent 定义的 `tools` 数组里引用的名字 |
| `description` | `""` | 自然语言描述，给模型看——写清"什么时候该调我" |
| `risk` | `"low"` | 风险分级 `low` / `medium` / `high`，见下方判断标准 |
| `approval_required` | `False` | 显式要求人工审批——不管风险等级，执行前必过人工确认 |
| `idempotent` | `False` | 幂等声明；**只有幂等工具才重试**（`max_retries` 生效的前提） |
| `timeout_seconds` | `30.0` | 单次执行超时，超时按失败处理 |
| `max_retries` | `0` | 重试次数上限；非幂等工具永不重试（重试静默烧钱、重复扣款都是真实事故） |

### 风险分级怎么判断

- **low**：只读查询类（查订单、查政策），不产生副作用；
- **medium**：外部系统写操作、调用第三方服务（MCP 远端工具默认中风险）；
- **high**：涉及资金、数据不可逆变更——**框架强制人工审批，不靠 Prompt 声明**。

### 审批链路

`risk="high"` 或 `approval_required=True` 的工具，执行前挂起等人工确认：

```text
调用前权限与参数检查 → 高风险走人工审批（SSE: tool.approval.requested）
→ 审批决定（工作台或业务系统调 POST /v1/approvals/{approval_id}/decide）
→ 通过才执行（超时/重试/幂等策略生效）→ 全程进 Trace
```

审批记录落 `approvals` 表：决定不随进程重启丢失、不重复、可审计回查。业务系统接审批回调的写法见[接入业务系统](./business-integration.md)。

### 签名冻结：改 Tool 签名不会悄悄影响历史版本

Agent 版本快照里记录了所用 Tool 的签名哈希（`signature_hash`）。运行期校验两条：

- 引用的 Tool 已注销 → Run 拒绝启动（`AGENT_VERSION_UNRUNNABLE`）；
- Tool 签名漂移（函数签名变了，与快照记录不一致）→ 同样拒绝启动。

这是**防止"悄悄改了工具行为，旧版本还照常跑"**的版本冻结机制。改了 Tool 的函数签名后，需要保存 Agent 新版本才能用上；历史 Run 的 Trace 仍如实记录当时的现场。

**边界**：Tool 只管单点动作；必须按固定步骤、多步串行的流程不要塞进一个 Tool，用[图编排](./graphs-teams.md)做代码级强制。

## Skill：行业标准的知识与流程包

**是什么**：Agent Skills 开放标准（SKILL.md）——核心是一个装有 SKILL.md 的文件夹：frontmatter 元数据（至少 `name` 和 `description`，`name` ≤ 64 字符小写连字符、`description` ≤ 1024 字符必填）+ 告诉 Agent 如何完成某类任务的指令；还可打包脚本、参考资料、模板等资源。存量 Anthropic 生态的 Skill 包**直接可用**，不写一行适配代码。

**加载是渐进披露三档**：

1. **发现**：启动时只加载每个 Skill 的 name + description（约 100 token），够判断"什么时候用"；
2. **激活**：任务命中描述时，才把 SKILL.md 正文读进上下文（建议 < 5000 token / 500 行内）；
3. **执行**：正文引用的 scripts/、references/、assets/ 文件按需加载或执行。

**目录结构**：

```text
skills/refund-sop/
├── SKILL.md          # frontmatter: name, description；正文：退款 SOP 步骤
├── scripts/check_policy.py
└── references/policy.md
```

### 两种装载方式

**1. 目录装载**：把符合标准的目录放进 `app/skills/`。

**2. zip 包上传**：把目录打成 zip，经工作台「Skills → 上传 Skill 包」或 HTTP 上传，自动解压到 `skills/{name}/` 并扫描入库：

```bash
curl -F "file=@refund-sop.zip" http://localhost:8000/v1/skills
```

zip 必须恰好含一个目录 + SKILL.md；同名 Skill 覆盖更新；zip 路径逃逸（zip slip）被拒绝。

### 分配到 Agent 的两种方式

- **工作台**：Skills 列表点「分配」→ 勾选 Agent → 保存（每个变化的 Agent 升一个版本，`skills` 数组增删本 Skill）。
- **定义**：在 Agent 定义的 `skills: [refund-sop]` 里登记。

运行期 `skill_load` 工具按 Agent 分配强制加载——未分配的 Skill 调 `skill_load` 被 403 拒绝。

### scripts/ 沙箱边界

Skill 与 Agent 定义同库版本化、可审计。scripts/ 里的代码在受控环境执行：超时强制终止；网络白名单标记传入子进程——**进程级网络隔离需容器/网关兜底**。Skill 与 Tool 的分工边界：Skill 是"指导"，模型大概率遵循但不保证——**必须按固定步骤执行的流程（退款、审批）不要交给 Skill 承载，用[图编排](./graphs-teams.md)做代码级强制**。
