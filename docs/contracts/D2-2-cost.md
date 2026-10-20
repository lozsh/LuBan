# 契约 D2-2：`lubanagent/observability/cost` 成本治理

> 来源：开发计划 D2-2、用户手册 §8、PRD F7（估算近似、KV-cache 不过度承诺）、技术方案 §13.6。
> 前置：D1-1（token_usages/cost_records/runs 表）、D1-4（BudgetGuard 事中熔断已落地）、D1-2（ModelDefinition.params 价格约定）。

## 范围

- `lubanagent/observability/cost/`：`estimate.py`、`pricing.py`、`report.py`、`advice.py`、`__init__.py`
- `lubanagent/persistence/repositories/cost.py`：**新增 CostRepository**（聚合查询，本单元授权）+ `persistence/repositories/__init__.py` 导出
- `tests/lubanagent/observability/cost/`

## 契约决策

1. **事前估算 + 事中熔断 + 事后归因**三段：事中熔断已在 D1-4 BudgetGuard 落地，本单元做事前估算与事后归因/报表/建议，并在 AgentRuntime 无关的前提下纯库函数化（API/工作台接入在 D1-3/D4-1 随时可用）。
2. **估算是近似值**（PRD F7）：chars/4 启发式 + 工具 schema 开销常数，不做 tokenizer 级精确；返回 `TokenEstimate`（core 已有类型）。
3. **价格来源**：`ModelDefinition.params["input_price_per_1k"]/["output_price_per_1k"]`（元/1k tokens，与 D1-4 预算契约一致）；无价格 → 该模型成本记 None，报表显示为未知而非 0。

## 接口契约

### pricing.py
```python
def prices_of(definition: ModelDefinition) -> dict[str, float] | None
    # params 里两个价格键齐全 → {"input": x, "output": y}；缺任 → None
def cost_for_usage(prices: dict[str, float] | None, usage: TokenUsage) -> float | None
    # prices None → None；(input_tokens/1000*input + output_tokens/1000*output)
```

### estimate.py
```python
CHARS_PER_TOKEN: int = 4
TOOL_SCHEMA_OVERHEAD_TOKENS: int = 200      # 每个绑定工具的 schema 近似开销
def estimate_tokens(definition: AgentDefinition, user_input: str, *,
                    history_chars: int = 0) -> TokenEstimate
    # input = (len(instructions 渲染后) + len(user_input) + history_chars) / CHARS_PER_TOKEN
    #        + len(tools) * TOOL_SCHEMA_OVERHEAD_TOKENS（上取整）
    # output = min(512, ModelCapability.max_output 无关——固定 512 近似)（近似值，注释声明）
def estimate_cost(definition: AgentDefinition, model: ModelDefinition, user_input: str, *,
                  history_chars: int = 0) -> float | None
    # estimate_tokens + prices_of 合成；无价格 → None
```

### report.py + CostRepository（persistence/repositories/cost.py）
```python
class CostRepository:
    def __init__(self, session: AsyncSession) -> None
    async def aggregate_by_agent(self, *, since: datetime | None = None) -> list[dict]
        # join runs+token_usages：每 agent_key 的 runs 数、input/output tokens 合计
    async def aggregate_by_session(self, agent_key: str | None = None) -> list[dict]
    async def aggregate_by_day(self, *, days: int = 7) -> list[dict]
        # 按 date(created_at) 分组（SQLite strftime('%Y-%m-%d')/MySQL DATE() 方言差异用 sa.func.date 统一）
    async def cost_rows(self, *, agent_key: str | None = None) -> list[dict]
        # cost_records join runs：model_key、cost 合计

class CostReport(BaseModel):
    by_agent: list[dict]; by_day: list[dict]; cost_by_model: list[dict]
    total_cost: float | None          # 有未知价格模型时为 None 并在 unknown_pricing_models 列名
    unknown_pricing_models: list[str]
async def build_cost_report(session: AsyncSession, *, days: int = 7) -> CostReport
```

### advice.py（优化建议：规则制，诚实最小集）
```python
def cost_advice(report: CostReport) -> list[str]
    # 规则（每条命中给一句具体建议，无命中给空列表）：
    # 1. 某 agent input_tokens/output_tokens > 10 → "X 输入占比过高（>10:1）：检查上下文是否膨胀，可启用摘要记忆或收紧窗口"
    # 2. 单 agent 日均 runs > 100 且用了高价模型 → 建议评估 cheap 槽位
    # 3. unknown_pricing_models 非空 → "为模型 X 补 params.input_price_per_1k/output_price_per_1k 以启用成本核算"
```

### __init__.py
re-export：prices_of/cost_for_usage/estimate_tokens/estimate_cost/CostRepository/CostReport/build_cost_report/cost_advice。

## 行为契约

1. 估算函数纯计算、无 IO（代码强制；可在 Run 发起前调用给出近似成本）。
2. 事中熔断 Trace 已有：测试断言撞预算的 Run 存在 `BUDGET_EXCEEDED` 事件（D1-4 产出，本单元复验）。
3. 报表聚合基于 DB 真实记录（与 evals 同口径：观测数据驱动）。

## 边界契约

- cost 包可 import core/config/persistence + pydantic/标准库；禁止 import runtime/api/app.*（估算只依赖 core 模型定义）。
- 不宣传 KV-cache 命中承诺（PRD F7/技术方案 §13.6 口径）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/observability/cost/` 全绿：
   - pricing：有/无价格两态；cost_for_usage 数值断言；
   - estimate：known 输入长度 → 期望 TokenEstimate（含工具开销项）；estimate_cost 无价格 → None；
   - report：构造 2 agent × 3 run 的 token_usages/cost_records → aggregate_by_agent 聚合值断言；aggregate_by_day 分天正确（跨天数据）；unknown_pricing_models 收集；
   - advice：三条规则各命中/不命中用例；
   - 熔断复验：用 D1-4 runtime 测试基建跑一个 max_tokens 极小的 Run → DB 有 BUDGET_EXCEEDED 事件（集成验证，可放 tests/lubanagent/observability/cost/test_breaker_link.py）。
2. 全量回归绿；`ruff check lubanagent/observability/cost lubanagent/persistence` 无错。

## 存疑项与决策

1. 报表 API/页面化 → 不在本单元（D4-1 工作台消费 build_cost_report）。
2. 跨方言日期分组 → `sa.func.date(created_at)`（SQLite/MySQL 通用）。
