<!--
TEMPLATE-LOCK: 本文件章节锁定为以下清单，禁止新增/改名/调序：
1. 定位
2. 接口契约
3. 数据模型
4. 行为（状态机/调用树，均无则写"无"）
5. 边界规则
6. 名词
7. 上下游
所有模块（含子域）统一使用本模板。文件名为真实模块名，放在 modules/ 目录下。
-->

# skill 模块

## 定位

`lubanagent/skill/`（F12）是兼容 [Agent Skills 开放标准](https://code.claude.com/docs/en/skills) 的 Skill 运行时：管理 `SKILL.md` 包目录的渐进加载（L1 元数据常驻 → L2 正文按需 → L3 资源按需）与 `scripts/` 受控执行，不做 Skill 市场（`lubanagent/skill/__init__.py:1`）。不存在独立聚合根数据库实体——领域模型 `SkillMeta`/`SkillPackage` 定义在 `lubanagent/core/skill.py`（跨包共用类型，供 `skill` 与 `persistence` 仓储共用，`lubanagent/core/skill.py:1`），本模块只负责"文件系统 ↔ 内存视图 ↔ DB"的读写与执行逻辑；持久化落在 `skills` 表（`SkillRow`，`lubanagent/persistence/models/misc.py:13-24`）。用户领地示例包见 `app/skills/refund-sop/SKILL.md`（架构设计 §2 目录树 `docs/design-docs/LubanAgent-architecture-design.md:156-157`）。

## 接口契约

### HTTP

| 方法 | 路径 | 说明 | 错误情况 |
|---|---|---|---|
| GET | `/skills` | 列出 `app.state.skill_registry` 内存视图中的全部 Skill 包（`lubanagent/api/routes/skills.py:36-41`） | `skill_registry` 未装配时返回空列表 `[]`（非报错，`skills.py:39-40`） |
| POST | `/skills` | 上传 zip 包（`multipart/form-data`，字段名 `file`）安装 Skill（`skills.py:44-57`） | 按 64 KiB 块读取，累计超过 10 MiB 立即 `VALIDATION_ERROR`，不调用 `install`；合格包再合并字节传入 registry（`skills.py:18-19、50-61`）；`skill_registry` 未装配 → `CONFIG_ERROR`（`skills.py:47-48`）；非法 ZIP/特殊成员/无 `SKILL.md`/多包/非法 name 或链接目标 → `VALIDATION_ERROR`（详见"边界规则"；路由读取和 ZIP 展开均有配额） |
| DELETE | `/skills/{key}` | 按 `key`（即 `meta.name`）删除 Skill 包目录（`skills.py:60-72`） | Skill 不存在 → `VALIDATION_ERROR`（`skills.py:65-70`）；`skill_registry` 未装配 → `CONFIG_ERROR`（`skills.py:63-64`） |

路由层不 `import` `lubanagent.skill` 模块本体，只经 `request.app.state.skill_registry` 取用（分层禁忌注释，`skills.py:1-7`）；`SkillRegistry` 由用户领地 `app/main.py` 挂载（见"上下游"）。

### RPC / MQ / Cron

无。

### Python API（本模块对外的核心契约）

| 类/函数 | 签名要点 | 说明 | 错误情况 |
|---|---|---|---|
| `parse_skill_md(path: Path) -> tuple[SkillMeta, str]` | 解析 YAML frontmatter（`name`/`description` 必填，`version` 缺省 `"0.0.0"`）+ Markdown 正文（`lubanagent/skill/loader.py:17-48`） | L1+L2 解析入口，`scan_skills_dir`/`load_body` 均复用此函数 | frontmatter 缺失/未闭合/非法 YAML/非映射/缺 `name`/缺 `description` → `LubanError(VALIDATION_ERROR)`（`loader.py:25-44`） |
| `scan_skills_dir(root: Path) -> list[SkillPackage]` | 扫描 `root/*/SKILL.md`，读取整份文档解析 frontmatter 并丢弃正文，且为全包内容 hash 读取各资源；常驻视图只保留 L1 元数据与资源清单，不常驻正文（`loader.py:78-106`） | `SkillRegistry.scan`/`install` 的底层扫描函数 | `root` 非目录 → 空列表；根下任一符号链接、包内符号链接（含 `SKILL.md`）或解析失败 → `VALIDATION_ERROR`，不跳过（`loader.py:51-55、83-94`） |
| `load_body(package: SkillPackage) -> str` | L2：读取 `SKILL.md` 正文（`loader.py:94-97`） | 渐进加载第二阶段，供 `SkillRegistry.make_load_tool` 的 handler 调用 | 同 `parse_skill_md` |
| `load_resource(package: SkillPackage, rel_path: str) -> bytes` | L3：读取包内资源文件字节内容（`loader.py:100-113`） | 渐进加载第三阶段（Agent 按需读 `references`/`assets` 内容） | `rel_path` resolve 后逃逸出包目录 → `VALIDATION_ERROR`（`loader.py:104-108`）；文件不存在 → `VALIDATION_ERROR`（`loader.py:109-112`） |
| `content_hash(package_dir: Path) -> str` | 全包文件（相对路径+内容）sha256 聚合摘要（`loader.py:58-68`） | 同 `name` 内容是否变化的判定依据，驱动 `SkillRepository.upsert` 的版本更新 | 包内符号链接 → `VALIDATION_ERROR`（`loader.py:51-60`） |
| `run_script(package, rel_path, *, args=None, timeout_seconds=30.0, network_whitelist=None, max_output_chars=10_000, env_whitelist=None, extra_env=None, limits=None) -> ScriptResult` | 异步执行包 `scripts/` 内脚本：`.py`→`sys.executable`，`.sh`→`/bin/sh`，其他→直接执行（`lubanagent/skill/sandbox.py:227-238`）；env 白名单 + POSIX 资源限额 + 超时杀进程组（详见"行为"与"边界规则"） | 受控执行沙箱唯一入口 | 脚本路径不在包 `scripts/` 子目录内 / 逃逸 → `VALIDATION_ERROR`（`sandbox.py:94-111`）；脚本不存在 → `VALIDATION_ERROR`（`sandbox.py:105-110`）；启动中或运行中超时不抛异常，返回 `timed_out=True` 的 `ScriptResult`；启动中或运行中取消则回收后重新抛出（`sandbox.py:339-400`） |
| `ScriptResult`（frozen dataclass） | `exit_code, stdout, stderr, duration_ms, timed_out`（`sandbox.py:83-91`） | `run_script` 的规范化返回值 | 无 |
| `DEFAULT_ENV_WHITELIST` | 常量 tuple（`sandbox.py:54-67`） | 默认环境变量白名单：精确名匹配、尾缀 `*` 前缀匹配（如 `LC_*`）——`PATH/HOME/TMPDIR/TEMP/LANG/LC_*/SYSTEMROOT/SYSTEMDRIVE/COMSPEC/PATHEXT/WINDIR`；宿主密钥类变量（`*_API_KEY`/TOKEN 等）一律不在白名单内，子进程默认不可见 | — |
| `ResourceLimits`（frozen dataclass，`sandbox.py:70-80`） | `cpu_seconds: int \| None = 10`（RLIMIT_CPU）、`memory_mb: int \| None = 512`（RLIMIT_AS）、`max_processes: int \| None = 64`（RLIMIT_NPROC）、`max_file_mb: int \| None = 64`（RLIMIT_FSIZE） | 子进程资源限额；`None` 表示不限制该项，全字段 `None` 即整体关闭限额（等价非 POSIX 平台行为）；`run_script(limits=...)` 可配可关 | — |
| `SkillRegistry.__init__(*, skills_root: Path, repository: SkillRepository \| None = None)` | 运行期视图入口；`repository` 为 `None` 时纯内存不入库（`registry.py:27-33`） | 由 `app/main.py` 实例化并挂 `app.state`（见"上下游"） | 无 |
| `SkillRegistry.scan() -> list[SkillPackage]` | 扫描目录；有 `repository` 时在本地 savepoint 中逐包 `upsert`，全部成功后才刷新内存视图（`registry.py:35-44`） | source of truth 是文件系统；DB 版本/manifest 只在内容 hash 变化时更新，且 savepoint 不替代调用方事务提交 | 扫描或 upsert 失败向上透传；内存视图保持旧值，仓储本轮变更回滚至 savepoint |
| `SkillRegistry.list_packages() -> list[SkillPackage]` | 返回内存视图全部包，供 `GET /skills` 直接读，不依赖 DB（`registry.py:46-48`） | 无 | 无 |
| `SkillRegistry.get(name: str) -> SkillPackage \| None` | 按 name 查内存视图（`registry.py:160-161`） | 无 | 找不到返回 `None`（非异常） |
| `SkillRegistry.list_metadata() -> list[SkillMeta]` | 返回全部包的 L1 元数据（`registry.py:163-164`） | 无 | 无 |
| `SkillRegistry.format_metadata_for_prompt(names: list[str] \| None = None) -> str` | 生成 `"- name: description"` 逐行文本，`names` 非 `None` 时按白名单过滤（`registry.py:166-172`） | 供 `capabilities.prepare` 注入 system prompt 尾部（`lubanagent/runtime/capabilities.py:117-120`） | 无 |
| `SkillRegistry.make_load_tool(*, allowed: list[str] \| None = None) -> RegisteredTool` | 构造 `skill_load` 工具：`{name} -> {body, scripts, references}`；`allowed` 非 `None` 时代码强制只放行清单内 Skill（`registry.py:174-211`） | 供 `capabilities.prepare` 装配进 `extra_tools`（按 Agent 分配 `definition.skills`） | 未分配 Skill → `LubanError(TOOL_PERMISSION_DENIED)`（`registry.py:191-197`）；Skill 不存在 → `LubanError(VALIDATION_ERROR)`（`registry.py:198-204`） |
| `SkillRegistry.install(zip_bytes: bytes) -> SkillPackage` | 根目录旁同文件系统暂存 ZIP，验证恰好一个包及安全目标后备份旧包再替换；`scan()` 成功后返回刷新包（`registry.py:50-91`） | 暂存/覆盖扫描失败恢复旧磁盘包与内存视图；仓储扫描本地 savepoint，不保证 DB/FS 全局事务 | 非法 ZIP、特殊路径/类型及 CRC 损坏（`registry.py:127-161`）、包数量非 1、非法或链接目标、暂存包内链接 → `VALIDATION_ERROR`；扫描/仓储异常原样透传 |
| `SkillRegistry.delete(name: str) -> None` | 与安装共用 `_destination` 校验；普通目录删除，不存在则 noop（`registry.py:93-125`） | HTTP `DELETE /skills/{key}` 的底层实现（调用方需自行 `scan()` 刷新，`skills.py:71-72`）；不删除 DB 旧行 | 空串、`.`/`..`、绝对路径、分隔符、链接或非目录目标及根外路径 → `LubanError(VALIDATION_ERROR)`（`registry.py:93-119`） |

## 数据模型

### `SkillMeta`（`lubanagent/core/skill.py:6-11`，Pydantic `BaseModel`）

L1 元数据：扫描常驻，注入 system prompt 供模型决策。

| 字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|
| `name: str` | ✓ | — | Skill 唯一标识，即 `SKILL.md` frontmatter 的 `name` 字段，也是 `skills.key` |
| `description: str` | ✓ | — | 一句话描述，`format_metadata_for_prompt` 直接引用 |
| `version: str` | — | `"0.0.0"` | frontmatter `version` 字段，缺省 `"0.0.0"`（`loader.py:45`） |

### `SkillPackage`（`lubanagent/core/skill.py:14-22`，Pydantic `BaseModel`）

L1+L2 定位信息：正文与资源按需读取，不常驻内存。

| 字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|
| `meta: SkillMeta` | ✓ | — | 内嵌元数据 |
| `path: str` | ✓ | — | 包目录绝对路径（`resolve()` 后，`loader.py:80、84`） |
| `scripts: list[str]` | — | `[]` | `scripts/` 子目录下文件相对路径清单（`loader.py:63-67、85`） |
| `references: list[str]` | — | `[]` | `references/` 子目录清单 |
| `assets: list[str]` | — | `[]` | `assets/` 子目录清单 |
| `content_hash: str` | — | `""` | 全包内容 sha256，版本判定依据（`content_hash()`，`loader.py:51-60、88`） |

### `SkillRow`（`lubanagent/persistence/models/misc.py:13-24`）

对应实体：`SkillPackage`（整体序列化进 `manifest` 字段，`repositories/skill.py:19`）。

| DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
|---|---|---|---|---|
| `id`（INTEGER） | — | ✓ | 自增 | 主键 |
| `key`（VARCHAR(128), UNIQUE） | `meta.name` | ✓ | — | 与 `SkillMeta.name` 相同值，仓储按此列查（`repositories/skill.py:20、40、44`） |
| `name`（VARCHAR(256)） | `meta.name` | ✓ | — | 冗余存一份 name，便于直接查询展示 |
| `version`（VARCHAR(32)） | `meta.version` | ✓ | — | 仅内容 hash 变化时才更新（`repositories/skill.py:34-36`） |
| `manifest`（JSON） | `SkillPackage.model_dump(mode="json")` | ✓（`default=dict`） | `{}` | `SkillPackage` 全量序列化，`repo.get`/`repo.list` 直接 `model_validate` 反序列化回来（`repositories/skill.py:41、45`） |
| `enabled`（BOOLEAN） | — | — | `True` | 每次 `upsert` 都跟随入参覆盖（`repositories/skill.py:33`），当前上层调用未见非默认值传入（`registry.py:42`：`upsert(package)` 未传 `enabled`）——`[TODO: enabled=False 的实际置位入口未在本模块范围内找到，SkillRegistry 未暴露启停 API]` |
| `created_at`（DATETIME） | — | ✓ | `func.now()` | 首次插入时间 |

索引：
- `key`（UNIQUE） — 同名 Skill 幂等 upsert 的查找键

## 行为

### 状态机

无（Skill 包无生命周期状态字段，`enabled` 目前只在 `upsert` 时被动跟随传参，无独立状态机）。

### 调用树

#### 渐进加载（L1 → L2 → L3）

三阶段均由调用方（`SkillRegistry`/`capabilities.prepare`/`skill_load` 工具）按需触发，代码层面不存在"自动升级"机制——每一层都是独立函数调用，上层决定是否往下走。

```
渐进加载
├── L1 元数据（扫描期读磁盘全文/资源散列，常驻内存仅元数据）
│   └── SkillRegistry.scan()
│       └── scan_skills_dir(root)  # loader.py:78-106；先拒绝根下及包内符号链接
│           └── 逐子目录 parse_skill_md(child/SKILL.md)  # 读整份 SKILL.md 解析 frontmatter，正文丢弃(_)；content_hash 再读全包文件
│               └── SkillPackage(meta, path, scripts=_list_rel(...), references=_list_rel(...), assets=_list_rel(...), content_hash=content_hash(...))
│   # 此阶段结果注入 system prompt: capabilities.prepare → skill_registry.format_metadata_for_prompt(definition.skills)
│   # 即"元数据先加载"发生在 Run 每次组装上下文时读内存视图, 不是每次重新扫描磁盘
├── L2 正文（模型决定"要看这个 Skill 详情"时触发, 经 skill_load 工具）
│   └── SkillRegistry.make_load_tool(...).handler(name)  # registry.py:191-209
│       ├── [name not in allowed] raise TOOL_PERMISSION_DENIED  # 代码强制的 Agent 级白名单
│       ├── [name not in self._packages] raise VALIDATION_ERROR
│       └── load_body(package)  # loader.py:94-97 → parse_skill_md(SKILL.md) 只取正文部分
└── L3 资源（正文内提到某个 references/assets 文件, 模型/工具再按需读取)
    └── load_resource(package, rel_path)  # loader.py:100-113
        ├── resolved = (base / rel_path).resolve()
        ├── [not resolved.is_relative_to(base)] raise VALIDATION_ERROR  # 防路径逃逸
        └── [not resolved.is_file()] raise VALIDATION_ERROR
```

触发时机说明：L1 在 `SkillRegistry.scan()`（应用启动钩子 `app/main.py:48` 调用一次，或 `install`/`delete` 后重新调用）时刷新；L1 内容通过 `capabilities.prepare` 在**每次 Run 组装上下文时**读取当前内存视图注入 system prompt（`lubanagent/runtime/capabilities.py:117-120`），本身不触发磁盘扫描。L2 只在模型主动调用 `skill_load` 工具时触发（`_SKILL_HEADER` 提示词明确引导"需要时先用 skill_load 工具加载正文再执行"，`capabilities.py:30`）。L3（`load_resource`）在当前仓库范围内**未见被 `skill_load` 工具或 `agent_loop` 自动调用**——`make_load_tool` 的 handler 只返回 `references`/`scripts` 的**清单**（相对路径字符串），不读取其内容（`registry.py:205-209`）；`[TODO: L3 资源内容的实际消费方（例如模型后续如何拿到 references 文件内容）未在 skill/runtime 模块代码中找到调用点，可能依赖模型侧另行发起文件读取工具或本仓库尚未接线]`。

#### Sandbox 受控执行（`run_script`）

```
run_script(package, rel_path, args, timeout_seconds, network_whitelist, max_output_chars, env_whitelist, extra_env, limits)
├── _resolve_script(package, rel_path)  # sandbox.py:94-111
│   ├── resolved = (base / rel_path).resolve(); scripts_root = (base / "scripts").resolve()
│   ├── [not resolved.is_relative_to(base) or not resolved.is_relative_to(scripts_root)]
│   │   └── raise LubanError(VALIDATION_ERROR)  # 双重校验: 既要在包目录内, 也必须在 scripts/ 子目录内
│   └── [not resolved.is_file()] raise LubanError(VALIDATION_ERROR)
├── 按后缀选解释器: .py → [sys.executable, script]；.sh → [/bin/sh, script]；其他 → [script]（直接执行）
├── env = _build_child_env(env_whitelist, extra_env, network_whitelist)  # sandbox.py:128-144
│   ├── 按白名单（缺省 DEFAULT_ENV_WHITELIST）从宿主环境筛选 → 宿主密钥默认不可见
│   ├── extra_env 显式注入项原样透传（含覆盖白名单同名变量，调用方显式授权）
│   └── env["SKILL_NET_WHITELIST"] = ",".join(network_whitelist or [])  # 白名单仅作为标记透传
├── [limits 生效项非全 None 且 POSIX] 构造 preexec_fn  # sandbox.py:261-298, fork 后 exec 前逐项 setrlimit
│   ├── RLIMIT_CPU = cpu_seconds（默认 10s, 超限收 SIGXCPU）
│   ├── RLIMIT_AS = memory_mb * 1024 * 1024（默认 512MB; macOS 内核一律 EINVAL → 运行期探测后跳过并记 warning, sandbox.py:147-164）
│   ├── RLIMIT_NPROC = UID 基线进程数 + max_processes  # RLIMIT_NPROC 按真实 UID 计数全部进程,
│   │   语义是"在当前 UID 已有进程数之上允许新增的配额"（默认 64）——防 fork 炸弹又不误杀存量; 统计失败跳过记 warning
│   └── RLIMIT_FSIZE = max_file_mb * 1024 * 1024（默认 64MB, 超限收 SIGXFSZ）
├── [非 POSIX 平台（无 resource 模块）] 限额整组跳过并记 warning, 不做假隔离（sandbox.py:264-269）
├── 设置单一 deadline，独立任务启动 asyncio.create_subprocess_exec(*cmd, *args, cwd=包目录, env=env, start_new_session=True)  # 独立进程组；shield 跟踪启动
│   ├── [启动中超时] 等待取得进程句柄后杀组并 wait，返回 timed_out=True
│   └── [启动中取消] 等待取得进程句柄后杀组并 wait，重新抛出取消
├── stdout/stderr 各起 _drain_output，持续排空，分别只保存前 max(0, max_output_chars)*4 字节；并发等待进程退出，剩余时限由同一 deadline 计算
│   ├── [正常完成] 解码保存的前缀，分别按 max_output_chars 截断
│   ├── [运行中超时] timed_out=True；杀独立进程组并等待管道任务/进程回收
│   └── [运行中取消/其他异常] 杀独立进程组、清理任务并重新抛出
└── return ScriptResult(exit_code, stdout, stderr, duration_ms, timed_out)  # 超额输出不主动杀进程
```

## 边界规则

- **脚本执行必须落在包 `scripts/` 子目录内**：`_resolve_script` 同时校验 `resolved.is_relative_to(base)`（包目录内）与 `resolved.is_relative_to(scripts_root)`（`scripts/` 子目录内）两条件，任一不满足即拒绝执行，代码强制（`lubanagent/skill/sandbox.py:94-111`）；测试覆盖 4 种逃逸/越权路径：`../outside.py`、`scripts/../../outside.py`、`references/guide.md`（包内但非 `scripts/`）、`SKILL.md`（`tests/lubanagent/skill/test_sandbox.py:82-98`）。
- **资源读取（L3）同样禁止路径逃逸，但校验范围是整个包目录而非仅 `scripts/`**：`load_resource` 只要求 `resolved.is_relative_to(base)`（包目录内即可，不限定子目录），比 `run_script` 的约束更宽松（`loader.py:100-108`）。
- **超时/取消回收**：`run_script` 用 `start_new_session=True` 建独立进程组；启动放入受跟踪的独立任务并经 `asyncio.shield` 等待，启动中取消或超时先等取得进程句柄，再杀进程组并 `wait`；启动期超时返回 `timed_out=True`，取消则重新抛出。启动后 stdout/stderr 排空与进程等待共用启动前设置的单一 deadline 的剩余时限；运行期超时在 POSIX 向组发送 SIGKILL、回收管道任务和进程后返回 `timed_out=True`，取消或其他异常也杀组并回收后重新抛出（`sandbox.py:128-155、339-400`；`tests/lubanagent/skill/test_sandbox.py:118-153`）。非 POSIX 分支只杀直接子进程；运行期杀后管道与进程等待有超时兜底，不能保证逃离进程组的后代都被回收，更不构成恶意脚本的安全隔离。
- **网络隔离诚实声明——非强隔离**：模块 docstring 如实区分"已加固 / 未隔离"（`sandbox.py:1-24`）——已加固：env 白名单、资源限额、超时杀进程组、路径逃逸拒绝、输出截断；**未隔离（剩余风险面）**：网络仍可达（进程级网络隔离需 iptables/netns 等 OS 级沙箱，`SKILL_NET_WHITELIST` 仅是传入子进程的白名单标记，沙箱本身不拦截任何网络请求）、文件系统仍可读写宿主可达路径（无 chroot/只读挂载），部署期由容器/网关兜底。docstring 明确警告：**未来开放非受信 Skill 包（第三方上传）前必须先补 OS 级网络隔离，否则恶意脚本可读取内网、外传数据**（`sandbox.py:17-22`）。
- **env 白名单——宿主密钥默认不可见（风险 R8）**：子进程不再继承宿主完整环境，只透传 `DEFAULT_ENV_WHITELIST` 白名单内的运行必需项（`PATH/HOME/TMPDIR/TEMP/LANG/LC_*` + Windows 系统变量，`sandbox.py:54-67`），宿主密钥类变量（`*_API_KEY`/TOKEN 等）默认不可见；调用方可经 `env_whitelist` 覆盖白名单、经 `extra_env` 显式注入额外变量（显式授权，原样透传含覆盖白名单同名变量）（`_build_child_env`，`sandbox.py:128-144`）。
- **资源限额（POSIX setrlimit，可配可关）**：`ResourceLimits`（`sandbox.py:70-80`）默认限 RLIMIT_CPU 10s / RLIMIT_AS 512MB / RLIMIT_NPROC（UID 基线+配额 64）/ RLIMIT_FSIZE 64MB，经 `preexec_fn` 在 fork 后 exec 前逐项 `setrlimit`（只做纯 syscall，满足 async-signal-safe，`sandbox.py:195-217`）；`run_script(limits=ResourceLimits(cpu_seconds=None, memory_mb=None, max_processes=None, max_file_mb=None))` 可整体关闭。平台差异如实声明：非 POSIX（Windows）无 `resource` 模块 → 整组跳过记 warning（`sandbox.py:264-269`）；macOS 内核对 RLIMIT_AS 一律返回 EINVAL → 内存限额跳过记 warning（运行期探测 `_rlimit_as_supported`，瞬态下探 soft 限额后立即还原，`sandbox.py:147-164`）；RLIMIT_NPROC 按真实 UID 计数全部进程（非仅本进程树），故语义是"当前 UID 已有进程数之上允许新增的配额"，UID 进程数统计失败也跳过记 warning（`sandbox.py:167-192、281-292`）——不假装隔离。
- **`network_whitelist` 参数只是标记透传**：代码层面 `network_whitelist` 只写入子进程环境变量 `SKILL_NET_WHITELIST`（逗号分隔字符串），**子进程脚本本身若不主动读取并遵守该变量，可以正常发起任意网络请求**——即白名单不是内核级/网络层强制，脚本可绕过（见上一条网络隔离声明）。
- **双流有界保存、持续排空**：stdout/stderr 各自异步读取到 EOF，只保留各自前 `max(0, max_output_chars)*4` 字节；超额输出被丢弃而非提前终止脚本，仍等待正常退出或超时。所留字节以 UTF-8 `errors="replace"` 解码，再分别按 `max_output_chars`（默认 10,000）截取字符前缀；`exit_code` 保持真实进程退出码（`sandbox.py:114-125、351-374`）。
- **Skill 包目录结构约定**：`SKILL.md` 必须存在于 Skill 目录根，YAML frontmatter 必须以 `---` 起止，且 `name`/`description` 两字段非空字符串（`version` 可缺省），否则整包解析失败并抛错，**不静默跳过**（`loader.py:25-44、71`；测试覆盖 6 种非法 frontmatter，`tests/lubanagent/skill/test_loader.py:41-57`）；`scripts/`、`references/`、`assets/` 三个子目录均为可选（`_list_rel` 对不存在的子目录返回空列表，`loader.py:63-67`）。
- **安装与删除共用安全目录名校验**：`_destination` 只接受单个 `[A-Za-z0-9_.-]+` 目录项，显式拒绝空串、`.`/`..`、绝对路径和路径分隔符；目标不得是链接或普通文件，`resolve()` 后必须严格在配置根目录内且不等于根（`registry.py:23-24、93-125`；`tests/lubanagent/skill/test_install.py:124-162、201-211`）。
- **ZIP 安全与展开配额**：`_safe_extract` 写入前拒绝空路径、绝对/盘符路径、反斜杠、空段及 `.`/`..`、符号链接等非普通类型，并校验目标严格处于暂存目录。最多 512 成员、每成员 10 MiB、总展开 50 MiB：先查中央目录的成员数/声明长度，再以同一 `ZipTracker` 流式核验每成员实际长度及累计展开字节（含目录），CRC/EOF 损坏转 `VALIDATION_ERROR`；验证异常时临时目录清理，不覆盖旧包；覆盖扫描失败按既有备份/回滚语义恢复，但两次目录移动间存在短暂不可见窗口，也不保证文件与 DB 跨介质事务（`registry.py:26-28、59-63、131-173`；`core/zip_quota.py:36-88`）。HTTP ZIP 输入另限 10 MiB（`api/routes/skills.py:18-19、50-61`）。
- **目录扫描不跟随符号链接**：根下任一链接及候选包内链接（包括 `SKILL.md`、资源）拒绝扫描；`content_hash()` 亦拒绝包内链接，防止散列和清单读取包外文件（`loader.py:51-60、78-104`）。
- **zip 包必须恰好含一个 Skill 包**：`install()` 校验暂存区 `scan_skills_dir` 结果长度恰为 1，多包或零包（无 `SKILL.md`）均拒绝（`registry.py:58-64`）。
- **覆盖失败恢复与版本视图**：`install()` 在配置根目录的父目录创建暂存/备份目录，正常同文件系统时经 `os.replace` 替换；校验通过后先移动旧包到备份、再移动暂存包到目标，随后 `scan()`；扫描或仓储 upsert 失败恢复旧磁盘目录与旧内存视图（`registry.py:50-91`；`tests/lubanagent/skill/test_install.py:213-277`）。`scan()` 仅在全部 upsert 成功后刷新内存；仓储扫描用本地 savepoint 回滚本轮写入，但不是磁盘与 DB 的跨介质全局事务，也不代替调用方 commit（`registry.py:35-44`；`tests/lubanagent/skill/test_registry.py:136-157`）。`SkillRepository.upsert` 仅当 `manifest.content_hash` 改变才更新 `version`/`manifest`；因此成功覆盖且内容变化后版本视图的 `path` 随目标目录重新扫描刷新，内容未变则 DB manifest 不更新（`repositories/skill.py:16-37`；`registry.py:76-84`）。
- **`skill_load` 工具的 Agent 级白名单是代码强制而非声明式**：`allowed` 参数非 `None` 时，handler 内部显式 `if name not in allowed: raise LubanError(TOOL_PERMISSION_DENIED)`，即便模型在 prompt 中"决定"加载某 Skill，未被分配（`definition.skills` 未包含该名）也会被拒绝（`registry.py:191-197`）。
- **框架工具与用户工具同名冲突时改名而非报错**：`skill_load` 若与用户注册的同名工具冲突，装配层（`capabilities.py`）将框架版重命名为 `luban:skill_load` 并记 warning，而非阻断启动（`lubanagent/runtime/capabilities.py:123-132`，属于 `runtime` 模块的装配逻辑，本模块只提供被装配的 `RegisteredTool`）。
- **删除不联动 DB 行**：`delete()` 经 `_destination` 校验后删除普通目录，不存在则 noop；DELETE 路由接着 `scan()` 刷新内存视图，但 `scan()` 只 upsert 扫描到的包，不清理 `skills` 表中已消失包的旧行（`registry.py:35-44、121-125`；`api/routes/skills.py:60-72`）。因此 `GET /skills` 的内存视图不再显示该包，DB `get/list` 仍可能返回含旧路径的记录；这是现有行为，未改为 DB 删除事务。

## 名词

| 术语 | 语义 |
|---|---|
| Agent Skills 开放标准 | 本模块声明兼容的外部标准：`SKILL.md`（YAML frontmatter + Markdown 正文）+ `scripts/`/`references`/`assets/` 三类可选资源子目录的包格式约定（`__init__.py:1`、架构设计 `docs/design-docs/LubanAgent-architecture-design.md:493`） |
| SkillPackage | L1+L2 定位信息领域对象：元数据 + 包路径 + 三类资源相对路径清单 + 内容 hash，正文/资源本身不常驻（`lubanagent/core/skill.py:14-22`） |
| 渐进加载 | L1（元数据，扫描期常驻）→ L2（正文，`skill_load` 工具按需）→ L3（资源，代码提供 `load_resource` 但当前仓库范围内未见自动消费方）三级按需加载策略，目的是控制注入 system prompt 的 token 成本 |
| 受控执行沙箱（Sandbox） | `run_script` 提供的"路径限定 scripts/ 子目录 + env 白名单（宿主密钥默认不可见）+ POSIX 资源限额（CPU/AS/NPROC/FSIZE，平台不支持项如实跳过）+ 硬超时杀进程组 + 输出截断 + 网络白名单标记（非强隔离）"执行环境，非 OS 级沙箱；网络可达与文件系统未隔离是如实声明的剩余风险面（`sandbox.py:1-24`） |
| ResourceLimits | `run_script` 的资源限额参数 dataclass：CPU 时间/地址空间/进程数配额/单文件写入大小四项，默认 10s/512MB/64/64MB，各项可 `None` 关闭、全 `None` 整体关闭；macOS 上内存项自动跳过（内核拒绝 RLIMIT_AS）（`sandbox.py:70-80`） |
| DEFAULT_ENV_WHITELIST | 子进程环境变量白名单：精确名匹配或尾缀 `*` 前缀匹配，默认只透传 `PATH/HOME/TMPDIR/TEMP/LANG/LC_*` 与 Windows 运行必需项；宿主密钥（`*_API_KEY` 等）默认不可见，可经 `env_whitelist`/`extra_env` 显式放行（`sandbox.py:51-67、118-144`） |
| SKILL_NET_WHITELIST | 传入子进程的环境变量名，承载 `network_whitelist` 参数的逗号分隔字符串；脚本是否遵守取决于脚本自身实现，代码不做网络层强制拦截（`sandbox.py:49、143`） |
| content_hash | 包全部文件（相对路径+字节内容）的 sha256 聚合摘要，是"同名 Skill 内容是否变化"的唯一判定依据，驱动版本号更新（`loader.py:51-60`） |
| zip slip | zip 包内成员用 `../` 等路径逃逸目标解压目录的攻击手法；`_safe_extract` 禁止特殊路径并逐成员 resolve 校验（`registry.py:127-158`） |

## 上下游

**上游依赖**（本模块消费外部数据/能力）：

| 依赖方 | 触发接口 | 依赖内容 |
|---|---|---|
| `lubanagent.core` | `SkillMeta`/`SkillPackage`（`core/skill.py`）、`ErrorCode`/`LubanError`（`loader.py:8`、`sandbox.py:15`）、`RiskLevel`/`ToolDescriptor`（`registry.py:15`） | 跨包共用领域模型与错误体系，本模块不重复定义 |
| `lubanagent.extension` | `RegisteredTool`（`registry.py:16`） | `make_load_tool` 返回值类型，供 `runtime.capabilities` 装配进 `extra_tools` |
| `lubanagent.persistence`（`persistence/repositories/skill.py`） | `SkillRepository.upsert/get/list` | `SkillRegistry.scan()` 有 `repository` 时逐包 `upsert` 入库版本化（`registry.py:35-44`）；**通过 `TYPE_CHECKING` 延迟 import 避免 `persistence → skill → persistence` 循环依赖**（`registry.py:20-21` 注释明确） |
| `app/skills/*`（用户领地示例：`app/skills/refund-sop/SKILL.md` + `scripts/refund_check.py`） | `scan_skills_dir(settings.skills_root)` | 真实 Skill 包内容来源；`refund-sop` 示例含 `name: refund-sop`、`description`、`version: "1.0.0"` frontmatter 及 5 步 SOP 正文，`scripts/refund_check.py` 是一个校验订单号格式（`^ORD-\d{4}$`）的独立 Python 脚本，符合"仅 `scripts/` 内、可被 sandbox 执行"的约定（`app/skills/refund-sop/SKILL.md:1-25`、`app/skills/refund-sop/scripts/refund_check.py:1-38`） |
| `lubanagent.config`（`Settings.skills_root`） | `app/main.py:36` 构造 `SkillRegistry(skills_root=Path(settings.skills_root))` | 扫描根目录配置来源，缺省 `app/skills`（`app/main.py:35` 注释） |

**下游影响**（本模块触发其他模块或外部系统）：

| 被影响方 | 触发接口 | 触发内容 |
|---|---|---|
| `lubanagent.runtime`（`runtime/capabilities.py`） | `capabilities.prepare(..., skill_registry=...)` 内调用 `format_metadata_for_prompt`/`make_load_tool` | 按 `AgentDefinition.skills` 白名单把 L1 元数据注入 system prompt 尾部 + 把 `skill_load` 工具挂进本次 Run 的可用工具集（`lubanagent/runtime/capabilities.py:25、69、117-133`） |
| `lubanagent.api`（`api/routes/skills.py`） | `GET/POST /skills`、`DELETE /skills/{key}` | HTTP 层薄封装本模块 `SkillRegistry` 的 CRUD 能力，路由不直接 import 本模块（经 `app.state` 取用） |
| `app/main.py`（用户领地装配根） | 应用启动钩子内 `await skill_registry.scan()`（`app/main.py:44-48`） | 应用启动时预热内存视图 + 首次入库；`app.state.skill_registry = skill_registry` 挂载供路由取用（`app/main.py:62`） |
| `lubanagent.persistence`（`skills` 表） | `SkillRepository.upsert` | 每次 `scan()` 把内容变化的包写入 `skills` 表（DB 是文件系统的派生视图，source of truth 是磁盘，`api/routes/skills.py:6` 注释） |
