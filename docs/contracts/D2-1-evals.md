# 契约 D2-1：`lubanagent/observability/evals` 评测体系

> 来源：开发计划 D2-1、用户手册 §7、PRD F6（"可用的评测起点"，不过度承诺）、技术方案 §13.4。
> 前置：D1-4（AgentRuntime/Run）、D1-7（TraceWriter/DB）、D1-8（CLI 骨架，`luban eval` 待回填）。

## 范围

- `lubanagent/observability/evals/`：`dataset.py`、`assertions.py`、`judge.py`、`runner.py`、`report.py`、`replay.py`、`__init__.py`
- `lubanagent/cli/main.py`：**回填 `eval_cmd`**（D1-8 骨架 → 真实现，本单元授权）
- `lubanagent/runtime/agent_loop.py`：**仅按契约决策 2 丰富 MODEL_CALL_COMPLETED 事件 payload**（若 D1-4 未含）
- `tests/lubanagent/observability/evals/`、`tests/lubanagent/cli/`（eval 用例回填）

## 契约决策

1. **评测基于真实运行观测**（PRD F6）：runner 真跑 `AgentRuntime`（测试期 = FakeLLM 种子的 Runtime），断言读 DB Trace（trace_events/token_usages），不是 mock 出来的假数据。
2. **录制重放的脚本来源**：Trace 的 `MODEL_CALL_COMPLETED` 事件 payload 须含模型输出（`{"text": str, "tool_calls": [...]}`）。若 D1-4 实现未带，本单元授权在 `runtime/agent_loop.py` 补齐该 payload（事件类型不变，仅丰富载荷）并同步 D1-4 相关测试断言。
3. **LLM-as-judge 的模型**：经 `BrainResolver` 解析 `smart` 槽位（测试环境 = FakeLLM，脚本返回 `{"score": 5, "reason": "..."}` JSON）；judge 输出解析失败 → 该断言记 `error` 而非 pass/fail。
4. **回归门禁**：`--compare` 对比两个版本（或对比基线报告文件），凡"基线 pass、本次 fail"即回归，`luban eval` exit code = 1（CI 可守门）；无回归 exit 0；运行错误 exit 2。

## 数据契约（测试集 YAML，用户手册 §7 形态）

```yaml
- case: 查不到订单要安抚并给替代方案     # str，用例名（必填）
  input: "我的订单 12345 怎么还没到"      # str（必填）
  expect_tools: [query_order]           # 可选：工具调用断言（期望被调的工具 key）
  expect_tool_args:                     # 可选：参数子集匹配
    query_order: {order_id: "12345"}
  expect_output:                        # 可选：输出断言
    contains: ["安抚", "替代"]           #   关键字全部出现
  judge:                                # 可选：LLM-as-judge
    criteria: "先安抚情绪，再给解决方案"
    pass_score: 4                       # 1-5 分制，>= 为 pass
```

```python
class EvalCase(BaseModel): name: str; input: str; expect_tools: list[str]=[]; expect_tool_args: dict[str, dict]={}; expect_output: ExpectOutput|None; judge: JudgeSpec|None
class ExpectOutput(BaseModel): contains: list[str]=[]
class JudgeSpec(BaseModel): criteria: str; pass_score: int = 4
class EvalDataset(BaseModel): key: str; name: str=""; cases: list[EvalCase]
```

## 接口契约

### dataset.py
```python
def load_dataset(path: Path) -> EvalDataset     # YAML list 形态；key 缺省=文件名去后缀；格式错 → VALIDATION_ERROR
```

### assertions.py
```python
class AssertionVerdict(BaseModel): kind: Literal["tool_call","output","judge"]; passed: bool; detail: str=""
def check_tool_calls(case: EvalCase, events: list[TraceEvent]) -> AssertionVerdict | None
    # expect_tools 空且 expect_tool_args 空 → None（未配置）；否则：TOOL_CALL_STARTED 事件的工具集合 ⊇ expect_tools；
    # expect_tool_args 逐键做参数子集匹配（payload.args 包含期望键值）
def check_output(case: EvalCase, output: str | None) -> AssertionVerdict | None
    # contains 全部命中（大小写不敏感）才 pass；未配置 → None
```

### judge.py
```python
class LlmJudge:
    def __init__(self, model: BaseChatModel) -> None
    async def score(self, *, criteria: str, input: str, output: str) -> tuple[int, str]
        # prompt 要求输出 JSON {"score": 1-5, "reason": str}；解析失败 → LubanError(VALIDATION_ERROR)
async def check_judge(case: EvalCase, judge: LlmJudge, output: str | None) -> AssertionVerdict | None
```

### runner.py
```python
class CaseResult(BaseModel):
    case: str; passed: bool; verdicts: list[AssertionVerdict]
    run_id: str; output: str | None; error: dict | None = None
class EvalRunner:
    def __init__(self, runtime: AgentRuntime, *, judge: LlmJudge | None = None) -> None
    async def run_dataset(self, agent_key: str, dataset: EvalDataset,
                          *, version: int | None = None) -> "EvalReport"
        # 逐 case：runtime.run(RunRequest(agent_key, user_input=case.input, version=version))
        # → 从 DB 读该 run 的 trace_events → 三类断言（配置了才执行）→ case passed = 全部 verdict pass 且 run 完成
        # 单 case 异常不中断整集（记 error、passed=False）
```

### report.py
```python
class EvalReport(BaseModel):
    dataset_key: str; agent_key: str; agent_version: int
    results: list[CaseResult]; created_at: datetime
    @property
    def pass_rate(self) -> float
    def to_json(self) -> str
    @classmethod
    def from_json(cls, data: str) -> "EvalReport"
class ComparisonReport(BaseModel):
    baseline: EvalReport; current: EvalReport
    regressions: list[str]        # 基线 pass、本次 fail 的 case 名
    improvements: list[str]
    pass_rate_delta: float
def compare_reports(baseline: EvalReport, current: EvalReport) -> ComparisonReport
```

### replay.py（录制重放，技术方案 §13.4）
```python
def script_from_events(events: list[TraceEvent]) -> list[Any]
    # MODEL_CALL_COMPLETED 按序提取 payload 的 text/tool_calls → ScriptedFakeChatModel script 条目（契约决策 2）
async def replay_run(run_id: str, *, db: Database, registry: Registry, settings: Settings) -> Run
    # 读原 Run 的 agent_key/version/input → 构造 script → 注册 fake 模型（replay 专用 key）并临时指映射
    # → 用新 Runtime 重跑 → 返回新 Run；新 Run 的工具调用序列应与原 Run 一致（验收断言点）
```

### CLI 回填（cli/main.py eval_cmd）
```
luban eval run <agent_key>@<version?> --dataset <path> [--compare <version|report.json>] [--save <report.json>]
# 打印逐 case pass/fail + pass_rate + （有 compare 时）regressions/improvements/delta；exit code 按契约决策 4
```

## 行为契约

1. 断言三类各独立生效、可组合；未配置的断言类别不参与判定（代码强制）。
2. 单 case 失败/异常不中断数据集（代码强制）。
3. 重放复现：`replay_run` 新 Run 的 TOOL_CALL_STARTED 序列 == 原 Run（代码强制）。
4. evals 不承诺"自动证明可靠"——报告只呈现断言事实与对比（PRD F6 口径）。

## 边界契约

- evals 可 import core/config/persistence/model/runtime/observability(trace) + pydantic；禁止 import api/app.*/graph/multiagent/mcp。
- judge 提示词属框架内部实现，不进用户领地。

## 验收标准（须实跑，全程 FakeLLM 零网络）

1. `pytest tests/lubanagent/observability/evals/` 全绿：
   - dataset 加载（含非法 YAML 报错）；
   - 三类断言各正负用例（工具未调→fail；参数不匹配→fail；contains 缺词→fail；judge 分数边界 3/4/5 与解析失败→error 语义）；
   - runner：2 case 数据集（一 pass 一 fail）→ pass_rate=0.5、fail case 不中断；
   - compare：基线 pass 本次 fail → regressions 命中、exit code 语义；
   - **录制重放**：跑一次 script=[tool_calls, 文本] 的 Run → `replay_run` → 新 Run 工具调用序列一致；
   - CLI：`luban eval run agent@1 --dataset x.yaml --save r.json` 输出含 pass_rate；`--compare` 有回归 → exit 1。
2. 全量回归绿；`ruff check lubanagent/observability/evals lubanagent/cli` 无错。

## 存疑项与决策

1. judge 输出解析失败语义 → `error` 中立（契约决策 3），不计 pass/fail。
2. 模型输出进 Trace payload 的补齐授权 → 契约决策 2。
3. 评测不写库（eval_results 表）？→ 决策：`--save` 存 JSON 文件；`EvalRepository.save_result` 同时落库（表已在 D1-1 建好，evals 落地即启用）。
