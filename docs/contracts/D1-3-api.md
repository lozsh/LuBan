# 契约 D1-3：`lubanagent/api` + `lubanagent/app` REST/SSE 接入与装配

> 来源：开发计划 D1-3、技术方案 §5.1（API Key 鉴权/速率限制）、架构设计 §2/§3.3、用户手册 §3（SSE 事件序列）。
> 前置：D0-2（Registry/AuthProvider/AuthRequest/Principal）、D0-3（Settings/configure_logging）、D1-1（仓储）、D1-4（AgentRuntime/sse_stream）、D1-7（resolve_backend/db_failure_sink）。

## 范围

- `lubanagent/api/`：`routes/agents.py`、`routes/runs.py`、`routes/models.py`、`routes/traces.py`、`routes/evals.py`、`routes/skills.py`、`routes/approvals.py`、`auth.py`、`deps.py`、`errors.py`、`schemas.py`、`__init__.py`（routes/__init__.py 同）
- `lubanagent/app/`：`factory.py`、`lifespan.py`、`__init__.py`
- `tests/lubanagent/api/`（httpx ASGI 测试，不起真实端口）

## 契约决策

1. **请求/响应模型集中在 `api/schemas.py`**（Pydantic v2），路由只做编排；`/v1/evals`、`/v1/skills` 本单元为 **stub**（501 语义之外的空列表/404 语义按数据层实际：evals 引擎 D2-1、skill 运行时 D3-3 落地后填实——stub = 路由存在 +  schema 完整 + 返回空列表/空对象，保证 OpenAPI 完整产出，对齐"API stub 先行"）。
2. **鉴权**：默认 `ApiKeyAuthProvider`（框架内置 AuthProvider）：`Authorization: Bearer <key>` 与 `settings.api_key` 比对；`settings.auth_enabled=False` → 全放行（Principal(subject="anonymous")）；用户注册的 AuthProvider 优先（`registry.get_auth()`）。**速率限制**：`RateLimiter` Protocol + 内存实现（每 API Key 每分钟 `settings.rate_limit_per_minute`），超限 → 429（RATE_LIMITED）。
3. **会话历史**：`POST /runs` 请求体可携带 `history`（OpenAI 风格 `[{"role":"user","content":...}]`），v1 不查库（D3-2 落地会话记忆）。
4. **审批端点**（D1-5 ApprovalManager 的 HTTP 面）：`GET /v1/approvals?run_id=`、`POST /v1/approvals/{id}/decide`——客服场景人工确认的 REST 入口。

## 接口契约

### schemas.py（节选关键模型）
```python
class RunCreateRequest(BaseModel):
    input: str                                   # 用户输入（必填）
    session_id: str | None = None
    version: int | None = None
    variables: dict[str, str] | None = None
    history: list[dict[str, str]] | None = None  # [{"role": "user"|"assistant"|"system", "content": str}]

class AgentDefinitionOut(BaseModel): ...  # = AgentDefinition 字段镜像（直接复用 core 模型亦可，路由 response_model=AgentDefinition）
class ModelDefinitionOut(BaseModel): ...  # 序列化时**脱敏**：不输出 api_key_env 以外的任何连接信息（api_key_env 只是变量名可输出）
class RunOut(BaseModel): ...              # Run 镜像
class TraceOut(BaseModel):
    trace: Trace
    events: list[TraceEvent]
class ApprovalOut(BaseModel): ...         # ApprovalRequest 镜像
class DecideRequest(BaseModel):
    approved: bool
    decided_by: str = ""
class ErrorOut(BaseModel):                # {"error": {code,message,details,retryable}}
```

### routes（全部前缀 `/v1`，JSON 错误一律 ErrorOut）
| 路由 | 方法 | 语义 | 错误 |
|---|---|---|---|
| `/agents` | GET | list_agents | — |
| `/agents` | POST | create_agent（body=AgentDefinition）→ 201 | 409 VALIDATION_ERROR（key 重复） |
| `/agents/{key}` | GET | get_agent | 404 AGENT_NOT_FOUND |
| `/agents/{key}/versions` | POST | create_version（body=AgentDefinition）→ 201 | 404/409 |
| `/agents/{key}/versions/{version}` | GET | get_version | 404 AGENT_VERSION_NOT_FOUND |
| `/agents/{key}/runs` | POST | 创建 Run：**SSE 流式响应**（`text/event-stream`，事件经 `sse_stream(runtime.stream(...))`） | 404/422（AGENT_VERSION_UNRUNNABLE）；鉴权 401/429 |
| `/runs/{run_id}` | GET | get_run | 404 RUN_NOT_FOUND |
| `/runs/{run_id}/cancel` | POST | runtime.cancel → {"cancelled": bool} | 404 |
| `/models` | GET | list_models（脱敏） | — |
| `/models` | PUT | upsert_model（body=ModelDefinition） | 422 |
| `/brain-mappings` | PUT | set_brain_mapping(env, profile, model_key) | 404 MODEL_NOT_FOUND |
| `/traces/{run_id}` | GET | TraceOut（trace + events 列表） | 404 RUN_NOT_FOUND |
| `/approvals` | GET | ApprovalManager.pending(run_id 过滤) | — |
| `/approvals/{approval_id}/decide` | POST | decide → {"status": "approved"\|"rejected"} | 404 VALIDATION_ERROR |
| `/evals` | GET | 报告摘要列表（dataset_key/agent_key 过滤 + limit，读 `EvalRepository.list_summaries`）——2026-09 R5 评测体系打通，原 stub 已填实 | — |
| `/evals/{result_id}` | GET | 报告详情（摘要 + 逐 case 结果，`EvalReportDetail`）；旧 `/evals/results` stub 路由已删除 | 404 EVAL_RESULT_NOT_FOUND |
| `/skills` | GET | stub：SkillRepository.list()（有数据则返回） | — |
| `/health` | GET | {"status": "ok"}（不带 /v1 前缀，无需鉴权） | — |

### auth.py
```python
class ApiKeyAuthProvider:   # 实现 AuthProvider
    def __init__(self, api_key: str | None) -> None
    async def verify(self, request: AuthRequest) -> Principal
        # api_key 为 None（未配置）→ 拒绝所有（CONFIG_ERROR 语义，启动期就应告警）；
        # Authorization: Bearer <key> 比对（hmac.compare_digest）→ Principal(subject="api-key-holder", claims={"auth": "api_key"})
        # 缺失/不匹配 → LubanError(AUTH_FAILED)

class RateLimiter(Protocol):
    async def allow(self, key: str) -> bool: ...
class InMemoryRateLimiter:   # 滑动窗口（每分钟 N 次，deque 实现）
    def __init__(self, per_minute: int) -> None

async def get_principal(request: Request, ...) -> Principal
    # FastAPI 依赖：auth_enabled=False → anonymous；registry.get_auth() 优先，否则 ApiKeyAuthProvider；
    # Request → AuthRequest 适配（headers/query_params 小写化）；再经 RateLimiter（失败 → RATE_LIMITED）
```

### deps.py（FastAPI 依赖注入）
```python
@dataclass
class AppContext:            # 挂在 app.state.ctx
    settings: Settings
    db: Database
    registry: Registry
    runtime: AgentRuntime
    approval_manager: ApprovalManager
    rate_limiter: RateLimiter

def get_ctx(request: Request) -> AppContext
def get_db / get_runtime / get_registry / get_approval_manager   # 便捷依赖
```

### errors.py
```python
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    AGENT_NOT_FOUND: 404, AGENT_VERSION_NOT_FOUND: 404, RUN_NOT_FOUND: 404, MODEL_NOT_FOUND: 404,
    AUTH_FAILED: 401, RATE_LIMITED: 429, TOOL_PERMISSION_DENIED: 403,
    AGENT_VERSION_UNRUNNABLE: 422, VALIDATION_ERROR: 422, CAPABILITY_MISMATCH: 422,
    BRAIN_NOT_MAPPED: 422, CONFIG_ERROR: 500, INTERNAL_ERROR: 500, ...
}   # 未覆盖码默认 500
def register_error_handlers(app: FastAPI) -> None
    # LubanError → ErrorOut + 状态码映射；RequestValidationError → 422 ErrorOut(VALIDATION_ERROR)
    # 未捕获异常 → 500 ErrorOut(INTERNAL_ERROR)，日志留 traceback（响应不含堆栈）
```

### app/factory.py + lifespan.py
```python
def create_app(*, settings: Settings | None = None, registry: Registry | None = None,
               db: Database | None = None) -> FastAPI:
    """装配根（代码强制顺序）：
    1. settings 缺省 get_settings()；configure_logging(settings.log_level, settings.log_format)
    2. db 缺省 create_database(settings)
    3. registry 缺省 default_registry；discover(settings.extension_packages)（约定优于配置扫用户扩展）
    4. observability = resolve_backend(settings, registry)，SafeBackend sink 接 db_failure_sink(db)
    5. runtime = AgentRuntime(db=db, settings=settings, registry=registry, observability=..., approval_manager=...)
    6. app.state.ctx = AppContext(...)；register_error_handlers(app)；挂载 routes（/v1 + /health）
    7. lifespan：启动期 alembic 不自动跑（迁移走 CLI/命令，D1-8），关闭时 SafeBackend.drain + db.close()
    """
```
- 种子数据（SEED_MODEL_DEFINITIONS/SEED_BRAIN_MAPPINGS 落库）不在 create_app 自动做——属 `luban init`（D1-8）。但提供 `async def seed_defaults(db: Database) -> None`（幂等 upsert）供 CLI 与测试调用，放 `app/lifespan.py` 或 `persistence` 侧均可（决策：放 `lubanagent/app/lifespan.py`）。

## 行为契约

1. `/openapi.json` 完整产出全部路由 schema（代码强制：测试断言路径集与关键模型）。
2. SSE 端到端：`POST /v1/agents/{key}/runs` 返回 `text/event-stream`，事件序列含 `run.started…run.completed`（用 FakeLLM 种子 Agent 实测）。
3. 鉴权可关：`auth_enabled=False` 无头也 200；开启且无/错 key → 401 ErrorOut(AUTH_FAILED)；超限 → 429。
4. 错误映射统一：业务错误不泄堆栈；HTTP 状态按 ERROR_HTTP_STATUS。
5. 关鉴权也能跑（方便接业务网关，开发计划验收原文）。

## 边界契约

- api/app 包可 import core/extension/config/persistence/model/runtime/tool/observability + fastapi；禁止 import graph/multiagent/mcp/skill/knowledge/memory（后续单元接入时再开放）、禁止 `import app.*`（装配经 discover + Registry）。
- `lubanagent/app` 不注册业务路由（架构 §3.3）；`lubanagent/api` 不含业务逻辑。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/api/` 全绿（httpx.ASGITransport，FakeLLM + tmp SQLite + seed_defaults）：
   - `/openapi.json` 200 且 paths 覆盖上表全部路由；
   - 端到端：POST 创建 Agent（tools=[]、smart 大脑）→ `seed_defaults` 后 POST runs → SSE 文本含 `event: run.started` 与 `event: run.completed`；
   - 鉴权三态：关/错 key 401/超速率 429；
   - 404/422 错误映射形状 = ErrorOut；
   - approvals：构造 pending 审批（直接操作 ApprovalManager）→ GET 列出 → POST decide(True) → 状态 approved；
   - traces/{run_id}：完成一次 Run 后 GET 返回 trace + events 非空；
   - models 脱敏：响应不含 api_key 值（字段里只有 api_key_env 名）。
2. 全量回归绿；`ruff check lubanagent/api lubanagent/app` 无错；`lint-imports` 无 broken（api/app 分层契约本批次同步加入 importlinter.toml）。

## 存疑项与决策

1. evals/skills 路由本单元为 stub（语义见契约决策 1）；skills GET 直接读 SkillRepository（D1-1 已建表）。
2. 迁移不随启动自动跑（显式 `luban migrate`，防多实例并发迁移）；`seed_defaults` 幂等供 CLI/测试。
3. 会话历史请求体携带（D3-2 前不过库）。
