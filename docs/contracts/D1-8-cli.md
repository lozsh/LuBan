# 契约 D1-8：`lubanagent/cli` luban 命令行

> 来源：开发计划 D1-8、用户手册 §1（YAML 导入/导出语义）、架构设计 §2。
> 前置：D1-1（仓储）、D0-3（Settings/defaults 种子）、D1-3（seed_defaults 所在处）。

## 范围

`lubanagent/cli/`：`main.py`、`yaml_io.py`、`__init__.py` + `tests/lubanagent/cli/` + pyproject `[project.scripts] luban = "lubanagent.cli.main:app"`（本单元授权改 pyproject）。

## 契约决策

1. **YAML 是 shorthand 形态**（用户手册 §1 示例），与 `AgentDefinition` 字段映射如下（导入导出双向同一映射，往返无损）：

| YAML 键 | AgentDefinition 路径 | 备注 |
|---|---|---|
| `name` | `key` + `name` | key 即 name（slug） |
| `description` | `description` | 可选 |
| `instruction` | `instructions` | |
| `brain` | `brain.profile` | fast/smart/cheap/vision |
| `fallbacks` | `brain.fallback_profiles` | 可选 list |
| `tools` | `tools` | |
| `mcp_servers` | `mcp_servers`（新增字段，见决策 3） | |
| `skills` | `skills` | |
| `knowledge` | `knowledge.sources` | |
| `memory` | `memory` | `session`→session=True；`session-summary`→session+summary=True；`none`→全 False |
| `limits.max_steps` | `boundary.max_steps` | |
| `limits.budget_tokens` | `boundary.budget.max_tokens` | |
| `limits.budget_cost` | `boundary.budget.max_cost` | 可选 |
| `limits.timeout_sec` | `boundary.timeout_seconds` | |
| `io_contract.input` | `io.input_type` | |
| `io_contract.output` | `io.output_schema`（"text"→None；dict→schema） | |

2. **`luban eval` 命令本单元只挂骨架**：评测引擎属 D2-1；本单元执行到未装配的引擎时显式报错 `"evals engine not ready (D2-1)"` 并以 exit code 2 退出——不静默假通过。D2-1 落地时回填实现。函数名用 `eval_cmd`（`@app.command(name="eval")`），不遮盖 Python 内建。
3. **`mcp_servers` YAML 键**：Agent 定义引用 MCP Server 登记键的清单——决策：**给 core `AgentDefinition` 增加 `mcp_servers: list[str] = []` 字段**（技术方案 §5.2「能力」列含 MCP Tool；core 模型演进安全——符合"版本演进只允许加字段"）。core/definition.py 与 D0-1 契约、序列化测试同步更新。

## 接口契约

### yaml_io.py
```python
def definition_from_yaml(data: dict[str, Any]) -> AgentDefinition
    # 按决策 1 映射；未知键 → LubanError(VALIDATION_ERROR, details={"unknown_keys": [...]})；
    # 缺 name/instruction → VALIDATION_ERROR；brain 非法值 → VALIDATION_ERROR
def definition_to_yaml(definition: AgentDefinition) -> dict[str, Any]
    # 同映射反向；空集合字段省略（tools=[] 不输出）；往返：from_yaml(to_yaml(d)) 字段级等于 d（status/version 除外）
def load_yaml_file(path: Path) -> dict[str, Any]      # yaml.safe_load；文件不存在/语法错 → VALIDATION_ERROR
def dump_yaml_file(data: dict[str, Any], path: Path) -> None   # safe_dump, allow_unicode, sort_keys=False
```

### main.py（typer）
```python
app = typer.Typer(name="luban", help="LubanAgent 命令行（初始化/迁移/导入导出/评测）")

@app.command()
def init(
    agents_dir: Path = typer.Option(Path("app/agents"), help="Agent YAML 目录"),
    database_url: str | None = typer.Option(None, envvar="DATABASE_URL"),
) -> None:
    """种子落库（seed_defaults：fake 模型 + 四槽位映射，幂等）→ 扫描 agents_dir/*.yaml 逐个导入：
    已存在的 key → 追加新版本（create_version）；不存在 → create_agent。打印每个 Agent 的 key@version。"""

@app.command()
def export(key: str, output: Path | None = None,
           version: int | None = None, database_url: str | None = None) -> None:
    """导出 Agent 定义为 YAML（缺省当前版）；output 缺省打印到 stdout。往返无损（验收）。"""

@app.command()
def migrate(database_url: str | None = typer.Option(None, envvar="DATABASE_URL")) -> None:
    """包 Alembic upgrade head（读仓库根 alembic.ini）。"""

@app.command(name="eval")
def eval_cmd(...) -> None:   # 骨架，见契约决策 2
```

## 行为契约

1. `luban init` 幂等：重复跑不报错，同名同内容 YAML 二次导入语义 = 追加新版本（版本兼容治理：版本只增不改）。
2. 往返无损：`export` 产物再 `definition_from_yaml` 得到的定义与库中字段级一致（代码强制：测试断言）。
3. CLI 是初始化/迁移/评测入口，不承担运行期职责（开发计划 D1-8 关键架构）。
4. 数据库不可达/URL 错误 → 友好报错 exit 1（不打堆栈）。

## 边界契约

- cli 可 import core/config/persistence/app（seed_defaults）+ typer/pyyaml；禁止 import runtime/api/graph 等运行期包。
- 种子与 YAML 语义以 core 模型为准，不复制字段逻辑（yaml_io 只做映射）。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/cli/` 全绿（typer.testing.CliRunner + tmp SQLite + tmp YAML 目录）：
   - `migrate`：空库 → upgrade head 成功；
   - `init`：写入示例 YAML（含 tools/limits/memory/io_contract 全键）→ 跑 init → 输出 `key@1`；改 YAML 加描述再 init → `key@2`；库中定义字段与 YAML 映射一致；`seed_defaults` 生效（models/brain_mappings 有 fake-chat 与四槽位）；
   - `export`：导出到文件 → `definition_from_yaml` 与库中定义字段级一致（往返无损）；导出不存在 key → exit 1 友好报错；
   - `luban eval`：exit code 2 且输出含 "D2-1"；
   - 未知 YAML 键/缺 name → VALIDATION_ERROR 语义报错。
2. `pip install -e .` 后 `luban --help` 可跑（scripts 入口生效）。
3. 全量回归绿（含 core 加 `mcp_servers` 字段后的序列化测试更新）；`ruff check lubanagent/cli` 无错。

## 存疑项与决策

1. `luban eval` 骨架策略 → 契约决策 2。
2. `AgentDefinition.mcp_servers` 加字段 → 契约决策 3（core 演进，"只加字段"原则）。
3. init 重复导入语义 = 追加版本（而非跳过）——与"改 YAML 必须重新导入才生效"（用户手册 §1）一致。
