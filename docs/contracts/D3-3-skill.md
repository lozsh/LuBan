# 契约 D3-3：`lubanagent/skill` Skill 运行时

> 来源：开发计划 D3-3、技术方案 §5.5（兼容 Agent Skills 开放标准、渐进加载、受控执行、不做市场）。
> 前置：D0-2（Registry）、D1-1（skills 表 + SkillRepository dict 仓储 TODO(D3-3) 待换）。
> **范围边界**：本单元只做 skill 包本体；Runtime 接线（元数据注入 + load_skill 工具）由主 agent 统一集成（D3-int）。

## 范围

- `lubanagent/skill/`：`loader.py`、`sandbox.py`、`registry.py`、`__init__.py`
- `lubanagent/persistence/repositories/skill.py`：把 dict 承载演进为 `SkillPackage` 领域对象承载（TODO(D3-3) 兑现，本单元授权）
- `tests/lubanagent/skill/`

## 契约决策

1. **标准兼容**：SKILL.md = YAML frontmatter（`name`/`description` 必填，`version` 可选）+ Markdown 正文；目录内 `scripts/`、`references/`、`assets/` 子目录按 Agent Skills 开放标准识别。无法解析 → VALIDATION_ERROR（不静默跳过）。
2. **渐进加载三级**（技术方案 §5.5）：L1 元数据（name/description，扫描常驻）→ L2 正文（命中后读 body）→ L3 资源（references/assets/scripts 按需读文件或执行）。
3. **受控执行（sandbox）**：`run_script` 用 `asyncio.create_subprocess_exec` 执行 `scripts/` 内文件，`timeout_seconds` 硬超时（代码强制，超时杀进程组）；**网络白名单**以环境变量 `SKILL_NET_WHITELIST`（逗号分隔主机）传入子进程环境，并在文档/注释诚实声明：进程级网络隔离需 OS 沙箱，本实现为"受控环境标记 + 超时 + 输出捕获"（技术方案只要求"受控环境执行（超时、网络白名单）"，本实现给接口与传递机制，真实网络隔离部署期由容器/网关兜底）。
4. **入库版本化**：扫描目录 → 每包 upsert skills 表（key=name、version=frontmatter version 或内容 hash、manifest=文件清单+frontmatter JSON）；同 name 内容变化 → 新版本记录（SkillRepository 语义升级：get/list 返回 SkillPackage）。
5. **命中策略**：v1 由模型决定（元数据注入 system prompt + `load_skill` 工具，D3-int 接线）；不做关键字自动命中。

## 数据契约

```python
class SkillMeta(BaseModel):        # L1
    name: str; description: str; version: str = "0.0.0"
class SkillPackage(BaseModel):     # L1+L2 定位信息（正文按需读）
    meta: SkillMeta
    path: str                       # 包目录绝对路径
    scripts: list[str] = []         # 相对路径
    references: list[str] = []
    assets: list[str] = []
    content_hash: str = ""          # 全包内容 hash（版本判定）
```

## 接口契约

### loader.py
```python
def parse_skill_md(path: Path) -> tuple[SkillMeta, str]     # frontmatter+正文；非法 → VALIDATION_ERROR
def scan_skills_dir(root: Path) -> list[SkillPackage]        # root/*/SKILL.md 逐个解析（L1+清单，不读正文）
def load_body(package: SkillPackage) -> str                  # L2：读 SKILL.md 正文
def load_resource(package: SkillPackage, rel_path: str) -> bytes
    # L3：防路径逃逸（resolve 后必须在包目录内，否则 PERMISSION_DENIED 语义 VALIDATION_ERROR）
def content_hash(package_dir: Path) -> str                   # 全包文件 sha256 聚合
```

### sandbox.py
```python
@dataclass(frozen=True)
class ScriptResult:
    exit_code: int; stdout: str; stderr: str; duration_ms: float; timed_out: bool
async def run_script(package: SkillPackage, rel_path: str, *,
                     args: list[str] | None = None,
                     timeout_seconds: float = 30.0,
                     network_whitelist: list[str] | None = None,
                     max_output_chars: int = 10_000) -> ScriptResult
    # rel_path 必须在包 scripts/ 内（校验，防逃逸）；解释器：.py→sys.executable，.sh→/bin/sh，其他→直接 exec
    # env 注入 SKILL_NET_WHITELIST；超时杀进程组 timed_out=True；输出截断
```

### registry.py
```python
class SkillRegistry:
    def __init__(self, *, skills_root: Path, repository: SkillRepository | None = None) -> None
    async def scan(self) -> list[SkillPackage]                 # scan_skills_dir + （有 repository 时）入库版本化（2026-07-23 实现期修正：仓储为 AsyncSession，异步化是唯一自洽解）
    def get(self, name: str) -> SkillPackage | None
    def list_metadata(self) -> list[SkillMeta]
    def format_metadata_for_prompt(self, names: list[str] | None = None) -> str
        # "- name: description" 清单（D3-int 注入；names 过滤，缺省全部）
    def make_load_tool(self, *, allowed: list[str] | None = None) -> "RegisteredTool"
        # key="skill_load"：args {name: str} → 返回 {"body": ..., "scripts": [...], "references": [...]}
        # allowed 非 None 且 name 不在内 → 错误（Agent 只可用分配的 Skill，§5.5"可按 Agent 分配"）
```

### __init__.py
re-export：SkillMeta/SkillPackage/parse_skill_md/scan_skills_dir/load_body/load_resource/content_hash/ScriptResult/run_script/SkillRegistry。

## 行为契约

1. 标准 SKILL.md 包零适配可用：放入一个符合标准的包目录 → scan 可见元数据 → load_tool 命中返回正文（开发计划验收原文语义）。
2. 路径逃逸被拒绝（`../` 等资源/脚本路径 → 报错，代码强制）。
3. 脚本超时强制终止且 timed_out=True；网络白名单经 env 传递（子进程内 `os.environ["SKILL_NET_WHITELIST"]` 可读）。
4. 同 name 内容变化 → scan 后库里 version/manifest 更新（内容 hash 判定）。

## 边界契约

- skill 可 import core/extension/persistence + pydantic/标准库；禁止 import runtime/api/app.*/model/mcp/knowledge/memory/graph/multiagent/langchain*。
- 不做 Skill 公共市场（技术方案 §5.5）；scripts 受控执行的边界如契约决策 3 诚实声明。

## 验收标准（须实跑）

1. `pytest tests/lubanagent/skill/` 全绿：frontmatter 解析（含非法）；scan 多级目录；load_resource 逃逸拒绝；run_script：stdout 捕获/exit_code/超时（sleep 脚本 → timed_out）/白名单 env 传递/输出截断；make_load_tool 经 ToolExecutor 执行（含 allowed 过滤拒绝）；scan 入库与内容变化更新。
2. 全量回归绿；`ruff check lubanagent/skill` 无错。

## 存疑项与决策

1. 网络白名单的真实隔离级别 → 契约决策 3（接口+传递机制先行，OS 级隔离部署期兜底，文档诚实声明）。
2. 命中策略 → 模型驱动（元数据+load 工具），关键字自动命中不做（决策 5）。
