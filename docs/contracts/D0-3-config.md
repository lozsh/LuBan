# 契约 D0-3：`lubanagent/config` 环境化配置（含日志）

> 来源：开发计划 D0-3、架构设计 §2/§3.3、技术方案 §5.1、AGENTS.md §4（监控 C 方案缺省 langfuse）。

## 范围

`lubanagent/config/`：`settings.py`、`defaults.py`、`logging.py`、`__init__.py` + `app/config/.env.example` + `tests/lubanagent/config/`。

## 接口契约

### defaults.py（常量，Pydantic 之外的普通模块）
- `DEFAULT_SQLITE_URL: str = "sqlite+aiosqlite:///./lubanagent.db"`
- `USER_EXTENSION_PACKAGES: tuple[str, ...] = ("app.tools", "app.graphs")`（约定优于配置的扫描入口；框架 import 期不依赖用户包，运行期由 `extension.discover` 动态导入）
- `FAKE_MODEL_KEY: str = "fake-chat"`
- `SEED_MODEL_DEFINITIONS: list[dict]`：一条 fake-chat 的 `ModelDefinition` dict（`provider="fake"`、`factory="fake"`、全能力 True、`params={"script": ["你好，我是 LubanAgent。"]}`）——零密钥兜底（D1-9 验收"零外部密钥环境全绿"依据）
- `SEED_BRAIN_MAPPINGS: dict[str, str]`：`{"fast": FAKE_MODEL_KEY, "smart": FAKE_MODEL_KEY, "cheap": FAKE_MODEL_KEY, "vision": FAKE_MODEL_KEY}`

### settings.py
```python
class Settings(BaseSettings):
    # 环境
    env: Literal["dev", "staging", "prod"] = "dev"          # env: LUBAN_ENV
    debug: bool = False                                     # DEBUG
    # 日志
    log_level: str = "INFO"                                 # LOG_LEVEL
    log_format: Literal["console", "json"] = "console"      # LOG_FORMAT
    # 数据库（async 驱动 URL）
    database_url: str = DEFAULT_SQLITE_URL                  # DATABASE_URL
    # 观测（C 方案）
    observability_backend: str = "langfuse"  # OBSERVABILITY_BACKEND（内置 langfuse/langsmith/none；用户注册名也可选址，未知名由 resolve_backend 报 CONFIG_ERROR。2026-07-23 D1-7 集成时由 Literal 演进为 str——Literal 会在 Settings 校验期挡住用户后端名）
    langfuse_host: str | None = None                        # LANGFUSE_HOST
    langfuse_public_key: str | None = None                  # LANGFUSE_PUBLIC_KEY
    langfuse_secret_key: str | None = None                  # LANGFUSE_SECRET_KEY
    langsmith_api_key: str | None = None                    # LANGSMITH_API_KEY
    # 接入鉴权（F8）
    auth_enabled: bool = True                               # AUTH_ENABLED
    api_key: str | None = None                              # API_KEY（框架默认 AuthProvider 用）
    rate_limit_per_minute: int = 600                        # RATE_LIMIT_PER_MINUTE
    # 业务接入
    biz_base_url: str | None = None                         # BIZ_BASE_URL
    # 用户扩展扫描包
    extension_packages: list[str]                           # EXTENSION_PACKAGES（逗号分隔），默认 list(USER_EXTENSION_PACKAGES)

    @property
    def sync_database_url(self) -> str: ...   # aiosqlite→pysqlite、asyncmy→pymysql（Alembic 用同步驱动）

    @classmethod
    def load(cls) -> "Settings": ...          # env 分层：先读 os.environ 的 LUBAN_ENV，再以 .env + .env.{env} 分层加载（后者覆盖前者，真实环境变量最高优先）
```
- `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")`；`load()` 是分层入口，直接 `Settings()` 也必须可用（只读 `.env`）。
- 提供模块级便捷函数 `get_settings() -> Settings`（`functools.lru_cache` 缓存，`load()` 路径）。

### logging.py（stdlib，无第三方依赖）
- `class JsonFormatter(logging.Formatter)`：输出单行 JSON，字段至少 `time`（ISO8601）`level` `logger` `message`，记录有 `extra` 键时并入。
- `def configure_logging(level: str = "INFO", fmt: Literal["console", "json"] = "console") -> None`：配置 root logger（stream handler），`fmt="json"` 用 JsonFormatter，否则控制台格式；重复调用不叠加 handler（代码强制：先清已有 handler）。

### __init__.py
re-export：`Settings`、`get_settings`、`configure_logging`、`defaults` 模块。

### app/config/.env.example（架构树 scaffold-seed 位置）
列出全部 env 键 + 中文注释 + 缺省值；注释说明"复制为仓库根 `.env` 使用"。

## 行为契约

1. 零配置启动：`Settings()` 在无 `.env`、无环境变量时成功，database_url 指向本地 SQLite 文件（代码强制，对应验收"缺省值保证零配置也能起"）。
2. 分层覆盖：`load()` 下 `.env.{env}` 的值覆盖 `.env`，真实环境变量覆盖文件（代码强制）。
3. `sync_database_url` 驱动替换只映射已知方言（aiosqlite/asyncmy），其他原样返回。

## 边界契约

- 所有运行期可变项走 env/DB，代码里不硬编码连接信息（AGENTS.md §4；密钥本体只允许来自环境变量，库与配置只存 `api_key_env` 名）。
- config 不读用户业务配置文件（架构 §3.3）；`extension_packages` 只是包名约定，不在 import 期导入用户包。
- 依赖方向：config 可 import core（下层），不 import 其他兄弟包。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/config/` 全绿：
   - 无 env 无文件 → `Settings()` 成功且 `database_url == DEFAULT_SQLITE_URL`、`observability_backend == "langfuse"`；
   - monkeypatch 环境变量 `DATABASE_URL`/`OBSERVABILITY_BACKEND=none` → 生效；
   - tmp_path 写 `.env` 与 `.env.staging` → `load()` 分层覆盖正确（用 monkeypatch.chdir 隔离）；
   - `sync_database_url`：aiosqlite→pysqlite 映射；
   - `configure_logging(fmt="json")` 后 log 一条，capture 输出为合法 JSON 且含 `level`/`message`；重复调用 handler 数不增。
2. `app/config/.env.example` 存在且覆盖 Settings 全部 env 键（测试或人工核对）。
3. `ruff check lubanagent/config` 无错。

## 存疑项与决策

1. 架构树未列日志模块，但用户开工指令显式要求"日志" → 决策：`config/logging.py`，stdlib 实现，不引 structlog（2026-07-23）。
2. `.env.example` 位置：开发计划 D0-3 只说"齐全"，架构树 scaffold-seed 列 `app/config/.env.example` → 以架构树为准，加载文件仍是根 `.env`（example 内注释说明）。
3. 观测缺省冲突（D1-7"缺省 langfuse" vs D0-3"noop 兜底"）→ 决策：settings 默认 `langfuse`；不可用时降级 noop 的行为属 D1-7 适配器层职责，本单元只保证零配置可加载。
