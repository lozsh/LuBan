# 契约 D3-5：`lubanagent/multiagent` 多 Agent 协作（一等卖点）

> 来源：开发计划 D3-5、技术方案 §10.6（子 Run 契约）、AGENTS.md §2/§4（编排 Run 与单 Agent Run 同库同治理同部署；不是后期扩展）。
> 前置：D1-4（AgentRuntime）、D3-4（GraphBuilder/GraphRunner/LubanCheckpointer/子 Run 语义）。

## 范围

`lubanagent/multiagent/`：`supervisor.py`、`team.py`、`state.py`、`__init__.py` + `tests/lubanagent/multiagent/`。

## 契约决策

1. **拓扑模型**：一个 supervisor（本身是 Agent，绑自己的定义版本与大脑）+ N 个成员 Agent；supervisor 决定"自己答"还是"委派给某成员"；成员调用是**子 Run**（parent_run_id=编排 Run）；可多轮委派直到 supervisor 给最终答复或触碰边界。
2. **实现载体**：复用 D3-4 图编排——team 编译为一个 CompiledFlow（supervisor 节点 + 成员 agent 节点 + 路由条件边），跑在 GraphRunner 上（checkpoint/中断/Trace/取消传播全部继承）。不重写第二套调度器。
3. **共享状态**（state.py）：`TeamState` = `{"input": str, "messages": list[dict], "delegations": list[DelegationRecord], "final_output": str|None}`；`DelegationRecord(agent_key, input, output, run_id)`。messages 为简化对话轨迹（role/content/agent_key），供 supervisor 决策上下文。
4. **治理同权**（开发计划关键架构）：每子 Agent 调用 = Runtime.stream 子 Run（绑各自版本与模型、进各自 Trace）；**预算熔断**：编排 Run 与父共享 BudgetGuard（D3-4 parent_ctx 传入的 guard 经 TeamRuntime 全程共用，成员消费累计到同一池，撞线整条停，§10.6"不允许子 Run 烧完父 Run 继续"）；**取消传播**：父 token 取消 → 图节点边界停（D3-4 已落地）；高风险成员可由 Agent 定义自身的 approval 机制暂停（D1-4 已落地，图层不重复）。
5. **路由实现**：supervisor 的结构化输出路由——每轮 supervisor 调用用 `with_structured_output`（或 JSON 指令 + 解析兜底）产出 `{"action": "delegate"|"answer", "agent": str|None, "input": str, "answer": str|None}`；解析失败 → 当 answer 处理（不炸编排）。委派轮次上限 `max_delegations: int = 8`（防 ping-pong，代码强制）。
6. **评测回归**：编排可评测 = D2-1 EvalRunner 以 `team:<name>` 为 agent_key 跑？不——评测以成员/入口形态跑（v1：评测回归通过"对编排 Run 的 Trace 断言"由 evals 的工具调用断言覆盖——team 跑在 GraphRunner，事件进同一 DB，D2-1 断言机制直接可用；本单元验收以"编排 Trace 可被 evals 断言读取"实证）。

## 接口契约

### state.py
```python
class DelegationRecord(BaseModel):
    agent_key: str; input: str; output: str; run_id: str
class TeamState(TypedDict, total=False):
    input: str
    messages: list[dict[str, str]]          # {"role": "supervisor"|agent_key, "content": str}
    delegations: list[DelegationRecord]
    final_output: str | None
```

### supervisor.py
```python
class SupervisorDecision(BaseModel):
    action: Literal["delegate", "answer"]
    agent: str | None = None
    input: str = ""
    answer: str | None = None

class SupervisorRouter:
    """supervisor Agent + 结构化输出解析 → 每轮决策。"""
    def __init__(self, *, agent_key: str, members: list[str],
                 max_delegations: int = 8) -> None
    async def decide(self, state: TeamState, *, runtime: AgentRuntime,
                     parent_ctx: RunContext) -> SupervisorDecision
        # 构造路由 prompt（成员清单+各自 description 取自定义库 + 当前 messages）
        # → runtime.run 子 Run（supervisor 自己的定义/大脑，parent_run_id 透传）
        # → 解析结构化输出（先 with_structured_output 语义，失败按 answer 兜底）
        # → delegations 数达 max_delegations → 强制 answer（payload 注明 reason）
```

### team.py
```python
class TeamBuilder:
    def __init__(self, name: str, *, description: str = "") -> None
    def supervisor(self, agent_key: str) -> "TeamBuilder"
    def agent(self, agent_key: str, *, description: str = "") -> "TeamBuilder"   # 成员（可多次）
    def route(self, *, max_delegations: int = 8) -> "TeamBuilder"
    def build(self) -> "Team"
        # 校验：supervisor 已设、成员 ≥1、成员不含 supervisor → 否则 VALIDATION_ERROR
        # 产出 Team（内含 CompiledFlow：entry=supervisor 节点；成员节点；条件边=路由决策）

@dataclass(frozen=True)
class Team:
    name: str; description: str; supervisor_key: str; member_keys: list[str]
    flow: CompiledFlow

class TeamRunner:
    """对外的运行入口（薄封装 GraphRunner，语义=编排 Run）。"""
    def __init__(self, *, db: Database, runtime: AgentRuntime,
                 registry: Registry | None = None) -> None
    async def run(self, team: Team, user_input: str, *,
                  thread_id: str | None = None,
                  parent_ctx: RunContext | None = None) -> GraphResult
```

### __init__.py
re-export：TeamBuilder/Team/TeamRunner/SupervisorRouter/SupervisorDecision/TeamState/DelegationRecord。

## 行为契约

1. **分流团队跑通**（开发计划验收形态）：triage（supervisor）+ faq + refund 三 fake Agent——
   - 问"你们退货政策是什么" → triage 委派 faq → final_output 含 faq 答复；
   - 问"我要退款" → triage 委派 refund → final_output 含 refund 答复；
   - 直接 greeting → triage 自己答（action=answer）。
2. **每子 Agent 调用进 Trace 且绑版本**：编排结束后 DB 中成员子 Run 的 parent_run_id == 编排 Run id，agent_version 为成员定义当前版（代码强制：测试断言）。
3. **整条预算熔断**：编排共享 BudgetGuard，max_tokens 小值 → 编排终止 end_reason=BUDGET_EXCEEDED，且成员消费累计进同一 usage（代码强制）。
4. **取消传播**：父 token 取消 → 编排 Run 终态 CANCELLED（D3-4 机制，本单元复验）。
5. **可评测**：编排产生的 trace_events 可被 D2-1 `check_tool_calls` 断言消费（实证一次）。
6. **防 ping-pong**：构造互相委派的两 fake 成员 → 达 max_delegations 强制收尾（代码强制）。

## 边界契约

- multiagent 可 import core/extension/config/persistence/model/runtime/tool/observability/graph + langchain_core/langgraph/pydantic；禁止 import api/app.*/mcp/skill/knowledge/memory/fastapi。
- 编排与单 Agent 同库同治理（AGENTS.md §4）：不引入第二套 Run 存储/治理路径。

## 验收标准（须实跑，FakeLLM + tmp SQLite）

1. `pytest tests/lubanagent/multiagent/` 全绿，覆盖行为契约 1-6 + builder 校验（无 supervisor/无成员/成员含 supervisor → VALIDATION_ERROR）+ TeamState 消息轨迹累积断言。
2. 全量回归绿；`ruff check lubanagent/multiagent` 无错。

## 存疑项与决策

1. 路由以 supervisor LLM 结构化输出决策（不做规则路由 DSL——demo 级；规则路由可经 D3-4 手写图达成）。
2. supervisor 自身是完整 Agent（可被知识/记忆/Skill 接线能力增强，D3-int 能力天然继承）。
3. `.route()` 目前只承载 max_delegations；更细路由策略（指定入口成员、扇出并行）记 TODO（后续演进，不阻塞）。
