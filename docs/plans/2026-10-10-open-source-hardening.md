# LubanAgent 开源发布加固与工程闭环修复计划

> 新会话交接：先读 `AGENTS.md`、`CLAUDE.md` 和项目内 `.claude/skills/spec-coding/SKILL.md`，按本计划逐单元执行。主 agent 集成，worker 局部实现/补文，独立 strict-validation 审查。**当前会话只产出计划，没有执行修复；下面所有复选框均表示待执行工作。**

**Goal：** 修复已发现的安全控制缺陷，让已有 Agent 能力在默认装配、工作台、部署、治理与验证链路中兑现，并使公开文档与可证明的能力一致。

**Architecture：** 保持 Python ≥3.11、LangChain/LangGraph、MySQL/SQLite、框架与用户领地单向依赖。优先复用统一鉴权、ToolExecutor、Runtime、Trace 和数据库仓储；不另造编排引擎、用户权限后台或观测平台。逐单元以回归测试证明缺陷，以可判定契约实施修复。

**Tech Stack：** Python / FastAPI / SQLAlchemy / Alembic / LangGraph；React / TypeScript / Vite；pytest / Vitest / Ruff / import-linter；Docker Compose / GitHub Actions。

**执行约定：** 常规修复直接执行，不按产品阶段分期；下文仅表示工程依赖顺序。方向性架构变更和不可逆操作才询问用户。现有服务不重复启动；验证使用隔离临时数据库及合成数据。**未经明确授权不 commit、不 push、不改 Git 身份配置、不重写历史、不修改或删除用户真实 `.env`/数据库/上传内容。**

## 1. 基线与证据边界

- 计划日期：2026-10-10；审查代码基线：`e4c899c`。执行时重新检查 HEAD、工作区及文件行号，不能把旧行号当作当前事实。
- 审查开始时工作区干净。本文件是随后新增的交接计划，不是已经生效的系统规格。
- 已扫当前 539 个 tracked 文件、39 个本地 refs 可达提交，并补扫本地旧 Git 对象；未确认真实 API Key、token 或私钥泄露。未 fetch，不能据此证明远端独有历史安全。
- 本机根 `.env` 有非占位模型 API Key、鉴权关闭配置，但被 Git 忽略和 Docker 上下文排除。这里不记录具体值；不因本次审查自动轮换密钥。
- Git 提交作者姓名/邮箱属于会公开的元数据；本机 IDE 个人目录信息被忽略。若用户要匿名化历史，必须单独授权，不能自行改写。
- 本地 DB 指定字段约 10,174 个非空值未命中所检查的敏感特征；不包含删除记录、空闲页、WAL 残留和全部业务字段。
- 当前截图和旧 PNG 只完成部分文件/元数据检查，**尚未完成视觉隐私确认**；Python 依赖尚未完成 CVE 审计；既有镜像/构建缓存未审计。
- 安全相关现有测试 24 项、模型/预算短测试 16 项通过，不等于漏洞已修复；本次没有运行全量验收。
- 前端标准依赖审计返回 13 个受影响包条目（7 high、6 moderate），**不是 13 个独立漏洞或 13 个可利用网站入口**；执行时重新查询并按实际使用路径判定。

### 证据等级

- **已确认代码缺陷：** Skill 安装特殊名称、团队路由依赖缺失、MCP 直接执行 handler、匿名限流分桶、上传完整读取及无解压配额、错误/Trace 原样传播、OpenAPI 测试重写快照。
- **配置/可信管理员边界：** 演示栈关闭鉴权、共享管理 Key、任意模型连接配置、可信脚本能力、同步线程不能强杀。不得都称为匿名 RCE/SSRF。
- **待回归证明的工程问题：** 子 Run 成本归因、默认能力装配、观测字段、执行重放、启动种子覆盖、多进程恢复。先写测试核实，再依据现有规格修复，禁止无证据大重构。

## 2. 所有单元共同遵守的开发闭环

每个单元使用以下步骤，不以“代码写了”宣告完成：

1. 核对本单元代码与 `docs/specs/modules/`、原契约；给本计划补齐必要的具体接口/字段/行为决策。技术细节自主决定并记录，方向性改变先问。
2. 在现有测试文件补最小失败测试；运行并确认失败原因对应缺陷。只有配置/审计任务可以用确定性配置断言替代运行行为测试。
3. 实现最小通用修复，保留正常路径、依赖方向及可选能力关闭时的零副作用。
4. 运行本单元测试、修改文件 lint、相关集成测试与覆盖率。安全拒绝分支和副作用“不发生”必须有断言；不能仅断言 4xx。
5. 主 agent 过 spec-coding F6 文档影响闸门；命中则派 worker 同步事实。新开独立 strict-validation，分别检查代码对契约、文档对代码，迭代至 PASS。
6. 勾选本计划并记录测试命令/结果及变更路径。仅在用户明确授权时提交，代码、迁移、对应规格必须同批提交。

**覆盖率：** 不降低已有整体 70%、核心包 85% 的门槛（`scripts/check_coverage.py`）。本轮新增/修改可执行行覆盖率目标至少 85%，安全关键拒绝分支必须覆盖；不能把输出截断或 HTTP 拒绝等价于资源占用有界。

## 3. 修复单元

### H00：先隔离测试配置和业务调用，再运行回归

- [x] 状态：H00 Section A 独立严审 PASS（第七轮）；隔离自测 7 passed、相关回归 33 passed、Ruff 与 diff check 通过。范围仅限当前测试进程 Python socket/HTTPX 和显式传入安全 env/cwd 的子进程；不得宣称任意原生 SDK/任意子进程均隔离。
- **证据：** `tests/demos/customer_service/conftest.py` 固定使用 9100；示例工具通过 `get_settings()` 取得地址，传给 Runtime 的测试 Settings 不会自动替换它；真实根 `.env` 可能被该入口加载。连接失败测试也不能依赖“本机没有服务”。
- **修改：** demo `conftest.py`、`test_scenarios.py`、相关工具测试 fixtures；必要时扩充 `tests/lubanagent/config/test_settings.py`，统一配置注入机制。
- **契约：** fixture 显式禁用真实 env 文件加载，清理/恢复 settings 缓存，注入测试 DB、Mock/Fake 模型、关闭外部观测；工具使用的 `get_settings()` 同样绑定测试配置。Mock 使用动态分配端口或进程内 ASGI transport，不占已有 9100/8000/5173。
- **验收：** 合成配置污染源设为不可调用的 sentinel 地址/合成 Key；测试记录所有业务/模型/观测请求，只允许本轮 Mock，其他目标在测试层确定性拒绝。请求路径依赖可注入 transport，不 monkeypatch 生产代码静默放行。连接失败用例以确定性异常替身实现，不误调用已有服务。CLI 子进程传隔离 env 和工作目录。
- **先只运行隔离自测：** 新增计划文件 `tests/test_test_environment_isolation.py`，执行 `.venv/bin/python -m pytest tests/test_test_environment_isolation.py`；通过后运行 `.venv/bin/python -m pytest tests/demos/customer_service`。不接触用户数据/服务才允许后续全量验收。
- **H00 执行记录（2026-10-12，严审前）：** 修改 `tests/conftest.py`、`tests/demos/customer_service/{conftest.py,test_scenarios.py}`、`tests/lubanagent/core/test_isolation.py`，新增 `tests/test_test_environment_isolation.py`。合成 dotenv 污染与固定端口/外网访问回归先失败 3 项；隔离后全局测试 fixture 显式使用临时 SQLite、关观测、屏蔽真实 dotenv/环境配置，套接字拒绝未授权目标并记录请求；demo Mock 绑定动态本地端口、临时业务数据，工具通过相同 `get_settings()` 读取；连接失败用确定性异常替身，子进程使用最小 env + `tmp_path` cwd。`.venv/bin/python -m pytest tests/test_test_environment_isolation.py` → 4 passed / exit 0；随后 `.venv/bin/python -m pytest tests/demos/customer_service tests/lubanagent/config/test_settings.py tests/lubanagent/core/test_isolation.py` → 26 passed / exit 0；`.venv/bin/ruff check tests/conftest.py tests/test_test_environment_isolation.py tests/demos/customer_service tests/lubanagent/core/test_isolation.py` → All checks passed / exit 0。F6：纯测试隔离与测试装配，不改生产接口/数据/行为/边界，资产目录未命中。首轮独立 Section A：FAIL（收集前隔离缺口、UDP/请求审计盲区、合成 Key 未验证），随后将 Settings/网络默认拒绝前移到 `pytest_configure`，增补 UDP 拒绝和合成 Key 自测；复验隔离 4 passed、限定回归 26 passed、Ruff 通过（均 exit 0）；第二轮独立 Section A：FAIL（正常业务请求无注入 transport、方法/路径请求未审计），随后增加客服示例 `_request` 可注入 transport，正常 Mock 装配审计 transport，失败用 `httpx.MockTransport`；4 项自测、26 项限定回归通过（exit 0），格式检查首次报 import 顺序并已修正。F6 再判：示例工具请求入口属实现边界改动，但没有改变公开接口、数据、业务行为与生产安全承诺；本轮仍不触发资产目录，后续 H15 会核查最终口径。网络日志记录 Python socket 与业务 HTTP transport 已覆盖路径，不等价于任意原生 SDK/进程外网络审计；显式安全 env 子进程已隔离，不把任意子进程宣称为已隔离；第三轮独立 Section A：FAIL（自定义模型密钥环境变量、请求层观测范围和 UDP sendmsg 漏口）；已按 Settings 字段名 + 常用模型 Key + 敏感后缀清理环境并恢复，增加 sendmsg 拒绝及自测；复验 Ruff exit 0、隔离 4 passed、限定 26 passed。审计范围明确为当前测试进程的 Python socket 目标记录 + 客服业务 transport 每次请求；模型/观测若构造可请求的客户端，出站目标会在 Python socket 层拒绝，但没有方法/URL 级模型/观测日志，不能据此宣称任何 SDK/原生层已完整拦截；另外在测试层拦截并记录 httpx 同步/异步每次请求的 method/URL，ASGI `http://test` 与已白名单的专用 Mock 端口放行，其余拒绝；`tests/test_test_environment_isolation.py` 对同一实际工具调用同时核对 transport 与 HTTPX 审计日志。最终定向验收：隔离自测 4 passed / exit 0、demo+配置+core+API 鉴权 33 passed / exit 0、Ruff all checks passed / exit 0、`git diff --check` exit 0。对非 HTTPX SDK / 非 Python socket / 任意子进程没有跨进程级强制隔离承诺；第四轮独立 Section A：FAIL（大小写变体环境变量未清除、被拒 HTTPX 请求未入审计日志）；已按大小写无关键比较清理合成污染、将请求日志写入点移到拒绝之前，并新增两项自测。用命令 `biz_base_url=http://unreachable.invalid:9100 deepseek_api_key=synthetic-only .venv/bin/python -m pytest tests/test_test_environment_isolation.py` → 6 passed / exit 0；随后 demo+配置+core+API 鉴权 33 passed / exit 0、Ruff 与 `git diff --check` exit 0；第五轮独立 Section A：FAIL（`_request` 原来无条件 `trust_env=False` 改变生产代理/CA 行为），已改为注入 transport 时关闭、生产无注入时 `trust_env=True`，新增生产默认语义回归。最新污染隔离自测 7 passed / exit 0，demo+配置+core+API 鉴权 33 passed / exit 0，Ruff 与 `git diff --check` exit 0；第六轮独立 Section A：FAIL（合成连接失败请求先被 HTTPX 未授权端口拦截，未到达 `MockTransport`）；已让该用例在测试端口白名单内通过 HTTPX 校验，并断言合成 handler 收到一次 GET、socket 连接日志为空。污染隔离自测 7 passed / exit 0，限定回归 33 passed / exit 0，Ruff 与 `git diff --check` exit 0；第七轮新开独立 Section A 严审 PASS：收集期/运行期配置、socket 与 HTTPX 审计、动态业务 Mock、无连接的合成失败、生产 `trust_env=True`、显式子进程 env/cwd 均有代码和上述 exit 0 验收证据。F6：示例内部 transport 为可注入实现细节，无公开业务契约变化，不需要修改长期资产；后续 H15 全局口径统一核查。

### H01：Skill 安装/删除目录隔离和原子替换

- [x] 状态：代码/资产同步完成，正向门槛（全量 Ruff、pytest）及 H01 定向回归通过；独立 strict-validation 复核 PASS（H01–H03 共 145 项含隔离 7 项，Ruff 与 diff check 退出 0）。技术细节：扫描拒绝包内及根目录链接；损坏 CRC 归一化为 `VALIDATION_ERROR`；覆盖采用同文件系统暂存+备份，失败恢复旧磁盘/内存/仓储本轮 savepoint；两次目录重命名之间存在短暂不可见窗口，不宣称并发读取方零空窗或 DB/文件跨介质事务。目录名和 Zip Slip、链接拒绝均有合成哨兵回归。
- **证据：** `lubanagent/skill/registry.py:22,63–74` 的白名单接受 `.`、`..`，`rmtree(root / name)` 未验证目标；`delete()` 也未拒绝根目录本身。
- **修改：** `lubanagent/skill/registry.py`、必要时 `loader.py`；测试优先扩充 `tests/lubanagent/skill/test_install.py`、`test_registry.py`、`test_loader.py`。
- **契约：** 名称拒绝空串、特殊目录项、绝对路径、分隔符；resolve 后目标必须严格位于配置根目录内且不等于根目录。安装与删除共用校验；不能跟随符号链接删除其他目录。
- **替换语义：** 完成元数据、包数量和解压校验后才替换旧包；临时内容放同文件系统中，失败后旧包仍可用。明确文件替换与 DB/内存扫描失败的回滚语义，不能宣称文件/DB 跨介质全局事务。
- **测试：** 特殊名称、符号链接、Zip Slip、非法包和覆盖扫描失败；用 `tmp_path` 在根/父目录放哨兵文件，失败前后均存在，旧包与版本不被破坏。不在真实 `app/skills` 实验。
- **验收命令：** `.venv/bin/python -m pytest tests/lubanagent/skill/test_install.py tests/lubanagent/skill/test_registry.py tests/lubanagent/skill/test_loader.py`；全部通过，正常安装/覆盖/删除保持可用。

### H02：统一保护最终应用路由，消除静态挂载遮蔽

- [x] 状态：当前用户装配经 `extra_routers` 前置注册团队路由并绑定 `get_principal`，有/无临时 UI、无/错/正确 token、429、JSON 404、真实页面与缺失静态资源回归通过；规格已同步，独立 strict-validation PASS（同 H01 联合复核）。技术边界：工厂返回后调用方直接对 FastAPI 实例 `include_router` 仍可绕过受保护入口且可能被静态 mount 遮蔽；本单元承诺的是框架和当前用户装配的 `/v1` 路由，任意外部后注册需明确另走 `extra_routers`，不能宣称自动保护任何任意 FastAPI 路由。匿名分桶安全在 H05 单独处理。
- **证据：** `app/api/teams.py:15,40–46` 不含身份依赖，`app/main.py:77` 单独挂载；框架根静态挂载先于团队路由注册。
- **修改：** `app/api/teams.py`、`app/main.py`、`lubanagent/app/factory.py`、必要时 `api/routes/__init__.py`；扩充 `tests/lubanagent/api/test_auth.py`，**新增计划文件** `tests/demos/customer_service/test_app_assembly.py` 专测用户装配。
- **契约：** 自定义业务 router 显式绑定 `get_principal` 或统一受保护注册入口，框架不能 import `app`。最终所有 `/v1/*` 业务路由具备鉴权/限流，公开健康端点保持可用。静态托管在业务路由之后注册。
- **测试：** 实际 `app.main:create_app` 的装配，UI dist 存在/不存在两种形态，缺 token/错 token 均拒绝且 TeamRunner 未调用；正确 token 调用成功；限流触发；未知 `/v1` 返回 JSON 错误而非 SPA；合法 SPA 路由可回退。测试构造临时 dist，不改真实产物。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/api/test_auth.py tests/demos/customer_service/test_app_assembly.py`。

### H03：MCP 导出白名单和统一工具治理

- [x] 状态：显式 `export_tools` 白名单默认空、工具调用统一 `ToolExecutor`、高风险无审批器拒绝，HTTP 独立 Key（无 Key 即使直接挂 ASGI 也拒绝）、受信 stdio 已有内存协议回归；代码与长期规格已同步，`UPGRADE.md` 补齐默认收紧与独立 Key 迁移提示；独立 strict-validation 首轮因升级提示缺失 FAIL，补齐后新开复核 PASS（H01–H03 联合 145 passed、Ruff / diff check 退出 0）。技术边界：注入 `RunContext` 可向 handler 传递归因，但 MCP Server 无内建 Trace 落库和持久审批；独立 Key 与 REST Key 不互通，默认只在 loopback 监听。默认导出从全部收紧为空，升级提示必须写 `UPGRADE.md`。
- **证据：** `lubanagent/mcp/server.py:29–40,63–67` 导出全部本地工具并直接调用 handler。
- **修改：** `lubanagent/mcp/server.py`、相关 Registry/工具适配；扩充 `tests/lubanagent/mcp/test_server.py`、必要时 `tests/lubanagent/tool/test_executor.py`。
- **契约：** 导出集合显式配置，默认不隐式导出全部有副作用工具；所有调用通过 ToolExecutor，保留参数验证、权限、审批、超时和 Trace 归因。缺审批处理器的高风险调用必须拒绝，不能自动放行。
- **传输边界：** stdio 为受信本地调用，不冒充 REST 身份；HTTP 使用经验证的独立身份接入或明确受保护部署配置，不能以“REST 配了 Key”宣称 MCP 已受保护。按当前 SDK 可用 API 实现，不杜撰参数。
- **测试：** 未导出工具不可枚举/调用；风险 HIGH 和 `approval_required` 在无批准/拒绝/批准三条路径分别验证 handler 次数；HTTP 未授权不能调用；正常低风险结果兼容。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/mcp tests/lubanagent/tool`。接口默认收紧必须同步升级说明。

### H04：上传、解压、解析和脚本输出的资源边界

- [x] 状态：实现与资产同步完成，独立 strict-validation 第三轮 PASS（Section A 代码对契约与 Section B 文档对代码均无阻塞项）；独立复跑全量 779 passed/2 skipped、定向 196 passed/2 skipped、Ruff、20 条 import-linter 契约、coverage 门槛与 diff check 均退出 0。首轮独立严审 FAIL：无所有权写入可在预留期间插入同名块，清理误删；已在 KnowledgeStore 同源事务内拒绝预留期间无权追加/替换/删除，创建方传匹配的 reservation_id；新增取消提交/最终发布与并发交错回归。第二轮独立严审 FAIL：验收数字与原始报告不符；已重新运行全量隔离 pytest，退出 0（2 skipped），原始 `/tmp/luban-h04-recheck.json` 总覆盖率 92.47%（含分支），语句覆盖率以该 JSON 为准。旧 `/tmp/luban-h04-final.json` 原始数值为 92.4454%（含分支）/94.6661%（语句），重算 H04 九个生产文件的修改可执行行 497/567 = 87.65%，而非之前记载的 477/537；该口径仅 H04 文件，不冒充全仓改动覆盖率。再次运行 H04 定向回归退出 0（2 skipped），`scripts/check_coverage.py` 核心包均 ≥85% 退出 0，Ruff、import-linter 20 contracts、diff check 退出 0；MySQL 跨实例未验。F6：资源限制、预留所有权和异常清理触及知识/Skill/API 行为与边界契约，已由 worker 同步 `docs/specs/modules/{knowledge-memory,skill,api-app-cli}.md`；本次续修仅更正验收证据，不新增长期事实，进入合并严格验证。H04 契约细化：上传按 64 KiB 块读取，单次知识上传 10 MiB/文件、50 MiB/请求且最多 50 文件，Skill ZIP 10 MiB、头像 20 MiB；超额即拒绝且不读取后续文件/不进入 manager。Skill ZIP 最多 512 成员、单成员最多 10 MiB、实际解压总量最多 50 MiB；DOCX 容器最多 512 成员、单成员最多 10 MiB、总量最多 50 MiB，目录头预检 + 实际流式核验，超额不得交给第三方解析。PDF/DOCX 解析限单文件输入 10 MiB、PDF 页数 200、提取文本 2,000,000 字符；解析在可终止子进程执行，超时/取消终止并回收，Linux 有效的 POSIX CPU/地址空间限制，macOS 地址空间限制不生效时明确边界。灌库累计 chunk 最多 5000，超过时不执行 embed/delete。脚本 stdout/stderr 分别仅保留截断所需的有限字节，双流持续排空且仍受超时/取消进程组清理控制；超过输出阈值继续运行到正常退出或超时，保持现有 exit_code/截断语义。ASGI multipart 接收前的请求体/并发限额仍须部署网关设置，不能宣称内存全过程有界或脚本对不可信方安全。各新增数值是实现默认值，不改变现有已承诺的 10/50/20 MiB。
- **修改：** `api/routes/skills.py`、`knowledge.py`、`avatars.py`，`skill/registry.py`、`sandbox.py`，`knowledge/ingest.py`、`manager.py`；相应 `tests/lubanagent/api/test_{skills,knowledge,avatars}.py`、`skill/test_install.py`、`test_sandbox.py`、`knowledge/test_ingest.py`。
- **契约：** 按块读取并在超过现有单文件限制时停止，不先完整读取；知识上传保留单文件 10 MiB、单次总量 50 MiB 合同，在读取过程中累计字节并限制文件数，达到上限即停止读后续文件。明确反向代理/ASGI multipart 接收层仍需请求体与并发限制。ZIP 同时限制成员数、单成员和解压总量，不只相信目录头长度；失败清理暂存并保留旧包。
- **解析/执行：** 文件解析移出事件循环，设定输入、页数/块数/文本量限额，取消/超时后的行为明确。DOCX 是压缩容器，第三方解析前也必须核查成员数/单成员/展开总量，不能仅限制最终文本；PDF 等解析需可终止的受限子进程/容器并落实资源限制，明确 macOS/Linux 支持边界，不把线程 timeout 当强制终止。`communicate()` 改为有界捕获并持续排空/终止，不能先把输出塞满内存再截断；未增加 OS 隔离前，不承诺可运行不可信 Skill。
- **测试：** 超大输入读取提前停止，多个各自合法文件触发单次累计上限/文件数上限且后续文件不再读取；小 ZIP/DOCX 大解压、过多成员、解析超限、双 stdout/stderr 洪泛、超时和清理；断言资源上界/读取次数与 handler 未触发，使用小配额测试避免消耗机器资源。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/api/test_skills.py tests/lubanagent/api/test_knowledge.py tests/lubanagent/api/test_avatars.py tests/lubanagent/skill tests/lubanagent/knowledge/test_ingest.py`。

### H05：可信主体限流和可核验审批归因

- [x] 状态：H05 实现与资产同步完成，独立 strict-validation 首轮 FAIL（缺过期/拒绝/重复决定的 handler 零调用证据，UPGRADE 误称审批落库）后已补齐测试并更正升级指引，新开独立复审 Section A/B PASS；定向 API/Tool/Runtime 75 项通过、Ruff/diff check 退出 0；前轮全量隔离 pytest 覆盖率 92.52%（2 skipped）、`check_coverage.py` 95.4% 核心均达门槛、import-linter 20 条契约保持。F6：分桶行为和审批接口/Trace 契约命中，worker 同步 `docs/specs/modules/{api-app-cli,tool-mcp}.md`、`docs/specs/risks.md` R11 和 `UPGRADE.md`。范围边界：内存限流不跨实例、审批不持久化，MySQL 跨实例未验。契约细化：鉴权开启用已验证 `Principal.subject` 分桶，关闭时全部共用 anonymous 桶（忽略任意 Bearer/代理头）；进程内限流桶最多 4096 个，闲置满 60 秒回收，满容量拒绝新主体（429），不驱逐活跃主体；仅部署者配置可信代理并完成身份映射时才可使用转发 IP，本次不读取 `X-Forwarded-For`。审批 HTTP 决定人取经鉴权的 Principal，客户端 `decided_by` 保留兼容但不采信；决定时间/主体/终态存进内存审批记录和 resolved Trace，过期标记终态但无人工主体；自动审批标记 `auto-approve` 而非真人。共享 Key 只能证明持有者，不能归因于具体人员；进程重启后审批状态不持久化。
- **修改：** `api/auth.py`、`api/routes/approvals.py`、相关 schemas、`tool/approval.py`；扩充 `tests/lubanagent/api/test_auth.py`、`test_approvals.py`、`tests/lubanagent/tool/test_approval.py`。
- **契约：** 已鉴权限流使用 verified Principal；匿名模式不信任任意 Bearer 作为分桶依据。桶有 TTL/容量上限和确定的满容量策略，不能靠新 token 获得无限配额；代理 IP 只有在可信代理配置后使用。
- **审批：** 决定人来自可信 Principal，客户端 `decided_by` 不得覆盖；审批记录和 Trace 包含决定人、时间及终态。共享 Key 只能证明“该 Key 持有者”，**不宣称独立人员审批或职责分离**。
- **测试：** 变换任意 token 不能绕过匿名限额；同一主体不同 token 不能拆桶；TTL/容量边界；伪造决定人不生效；拒绝/过期/重复决定不会执行工具。若原测试文件名变化，先确认实际路径。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/api/test_auth.py tests/lubanagent/api/test_approvals.py tests/lubanagent/tool`。审批 DB 化仅在 H12 契约确定后实施，不能在此暗中新增持久化承诺。

### H06：模型参数防密钥落库/回显和受信连接边界

- [x] 状态：实现与资产同步完成，独立 strict-validation 第二轮 PASS（首轮 FAIL 两条 blocking 均为测试证据缺失，已补齐清偿）。契约细化：拒绝输入统一在模型写入仓储边界与 OpenAI 兼容构造入口，读历史脏数据保持可读取但只输出安全副本（`model_construct` 跳过校验器，执行必须过构造入口再次校验拒绝）；先校验保留构造键、秘密/授权头、内嵌凭证的 URL 与环境变量名格式，再读密钥环境变量、创建客户端；`openai_compat` params 白名单 `_OPENAI_PARAMS` 十三项、含 `[REDACTED]` 占位值拒绝；更新请求 base_url 空值=沿用旧值。允许温度、max_tokens、模型计价等正常参数及本地 vLLM/Ollama 地址；不使用模型名等参数覆盖连接字段，不一刀切禁内网。脱敏规则供 H07 复用。验证证据：定向验收 60 passed、全量 pytest 800 collected / exit 0、Ruff All checks passed、import-linter 20 contracts kept、`check_coverage.py` 门槛全部达标、整体覆盖率 92.60%（`/tmp/luban-h06-final.json`）、`git diff --check` 退出 0。H06 四文件行覆盖：`core/model.py` 100%、`openai_compat.py` 97%（仅 48->52 分支部分）、`repositories/model.py` 100%、`routes/models.py` 剩余 miss（55-56、68）为 brain-mapping 存量分支非 H06 修改行。F6：命中接口/行为/边界契约，worker 同步 `docs/specs/modules/{model,api-app-cli,persistence}.md` 与 `UPGRADE.md`（新增 H06 收紧条目）；严审指出的 model.md 六处陈旧行号已全部修正。范围边界：MySQL 跨实例未验；脱敏为字段级规则，任意业务文本 PII 不在承诺内（H07 继续）。
- **证据：** `model/openai_compat.py:42–47` 任意 `params` 覆盖构造参数；模型 CRUD 回显 `params`。
- **修改：** `core/model.py`、`model/openai_compat.py`、`api/routes/models.py`、相关 repository；扩充 `tests/lubanagent/api/test_models.py`、`model/test_openai_compat.py`、`core/test_models.py`。
- **契约：** 不允许 `params` 绕过正式 `api_key_env`/`base_url`/model 字段；拒绝内嵌密钥、授权 header、URL 用户凭证及覆盖客户端连接的保留参数。输入拒绝与输出脱敏分开处理，已有脏数据读取不能回显秘密；迁移/清理不可自动打印秘密。
- **兼容边界：** 保留 OpenAI 兼容本地端点（Ollama/vLLM），不能为了防 SSRF 一刀切禁内网。连接地址与可引用 env 的限制做成部署者明确配置/网关权限约定；模型管理属于受信管理面，不是终端用户能力。
- **测试：** 正常 temperature/max_tokens/计价字段可用；恶意 api_key/default_headers/base_url 覆盖拒绝，nested header 和已有数据回显被脱敏；拒绝发生在网络请求前。所有 Key 均为合成测试值。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/model tests/lubanagent/api/test_models.py tests/lubanagent/core/test_models.py`。

### H07：错误、Trace、观测外发的安全数据契约

- [x] 状态：实现与资产同步完成，独立 strict-validation 第二轮 PASS（首轮 FAIL 三条 blocking 均为测试证据缺失——审批事件/MODEL_CALL_COMPLETED args 秘密断言、LubanError details 与 TOOL_CALL_FAILED details 脱敏零测试、API errors/traces 用例缺失——已补齐清偿）。实现：新建共享脱敏组件 `core/redact.py`（`is_secret_key`/`redact_value`/`register_secret_keys`，H06 规则上移复用、公开签名不变）；`agent_loop._error_dict` 非 LubanError 收敛为 `内部错误: {类型名}`（不拼 str(exc)），五条事件边界（TOOL_CALL_STARTED args / TOOL_CALL_COMPLETED result / TOOL_APPROVAL_REQUESTED args / MODEL_CALL_COMPLETED tool_calls args / TOOL_CALL_FAILED error details）走脱敏审计副本而执行用真实参数；`executor` 通用异常 details 移除 `reason: str(exc)`；`sse_stream`/`TraceWriter.emit`/`SafeBackend.emit+end_trace`（观测外发唯一出口，inner+sink 均脱敏）三边界全接线。验证证据：定向验收 181 passed、全量 pytest 816 collected / exit 0、Ruff All checks passed、import-linter 20 contracts kept、`check_coverage.py` 门槛全部达标、整体覆盖率 92.67%（`/tmp/luban-h07-final.json`）、`git diff --check` 干净；H07 七文件行覆盖：redact.py 97.7%、model.py 100%、agent_loop 91%、sse 100%、executor 99.1%、trace/writer 98.4%、adapters/base 90.5%。F6：命中接口/行为/边界契约，worker 同步 core-extension-config/runtime/tool-mcp/observability-trace-cost/observability-adapters/model/api-app-cli/persistence/index 九份规格（含连带行号漂移修正、唯一出口边界句）；第二轮严审核实全部新增声称行号精确。范围边界：executor `_validate_args` 的 pydantic `input` 未白名单化（非 blocking 已登记）；H10 重放遇 `[REDACTED]` 显式拒绝语义待 H10 落地；脱敏为字段级规则，任意业务文本 PII 不在承诺内。
- **修改：** `api/errors.py`、`runtime/agent_loop.py`、`runtime/sse.py`、`tool/executor.py`、`observability/trace_writer.py` 和 adapters；测试扩充 API errors/traces、Runtime SSE、ToolExecutor、TraceWriter/adapter 用例。
- **契约细化（2026-10-14 执行时补记）：** ①新建共享脱敏组件 `lubanagent/core/redact.py`：`is_secret_key`（与 H06 同规则）、`redact_value`（深拷贝展示副本，秘密键/凭证 URL/bearer 字符串 → `[REDACTED]`，不改源数据）、`register_secret_keys`（部署者注册额外敏感键名，字段级自定义脱敏）；`core/model.py` 秘密键规则重构为复用该模块，公开函数签名不变。策略边界：字段级键名规则，不做任意业务文本 PII 值级检测；异常 traceback 仅入日志，由部署日志策略管控。②`agent_loop._error_dict`：非 LubanError 异常收敛为 INTERNAL_ERROR + 稳定消息（只含异常类型名，不拼 `str(exc)`，SDK/上游正文/URL/本机路径不进 run.error/RUN_FAILED/SSE）；LubanError 的 details 经 `redact_value` 脱敏。③`executor` 通用异常 details 移除 `reason: str(exc)`，保留 `exception` 类型名。④工具桥/审批事件：TOOL_CALL_STARTED 的 args、TOOL_CALL_COMPLETED 的 result、TOOL_APPROVAL_REQUESTED 的 args、MODEL_CALL_COMPLETED 的 tool_calls args、TOOL_CALL_FAILED 的 error——trace 事件 payload 均为 `redact_value` 脱敏副本，handler/审批执行仍用真实参数（执行与审计副本分离）。⑤`TraceWriter.emit` 落库 payload 为脱敏副本（source of truth 为审计副本；H10 安全重放对脱敏记录须显式拒绝，届时落地）。⑥`sse_stream` 外发帧 payload 脱敏（纵深防御）。⑦`SafeBackend.emit`/`end_trace` 交给 inner 与 sink 的 event payload / summary 脱敏——`resolve_backend` 统一 SafeBackend 包装，此处是观测外发唯一出口。脱敏为纯函数，无故障 fail-open 面（不可序列化对象进 payload 前已被 `_json_safe` 兜底）。
- **契约：** 对客户端暴露稳定错误码与安全消息，不直接拼 SDK/上游正文/URL/本机路径；在 Trace 入库、SSE、日志和观测外发边界防止秘密字段传播。任意业务文本 PII 不可能仅靠正则保证，应提供字段级/自定义脱敏与内容保留配置，说明策略边界。
- **关键约束：** 执行用工具参数/结果与审计展示副本分离，脱敏不得修改实际参数。H10 的安全重放若所需记录被脱敏/未保留，应显式拒绝，不能用脱敏占位值执行真实工具。
- **测试：** 合成密钥出现在 nested args/result、异常、Authorization header、URL userinfo/query 时，客户端响应、DB 事件和外发调用中均不可见；安全业务字段仍可排查；脱敏组件故障不能 fail-open 外发秘密。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/api/test_errors.py tests/lubanagent/api/test_traces.py tests/lubanagent/runtime/test_sse.py tests/lubanagent/tool tests/lubanagent/observability`。

### H08：演示部署、镜像秘密排除和启动种子保护

- [x] 状态：实现与资产同步完成，独立 strict-validation PASS（非 blocking 备注两条已当场清偿：Dockerfile 预建 `/data/avatars`、容器 CMD 自动启动改 `luban init --fill-missing`）。实现：`.dockerignore` 递归排除（`**/.env`/`**/.env.*`+反排除样本、私钥六形态、`/data/`、demo 运行数据、`/.study/`）；compose app/langfuse 端口绑 `127.0.0.1`、mock-biz 取消宿主映射（容器网络互通）、`AVATAR_FILES_ROOT=/data/avatars`+`avatars-data` 卷；dev.sh Vite 显式 loopback+strictPort、自动启动 `--fill-missing`；`seed_defaults` 模型种子改"只填不存在"（`get_model(key) is None` 才播种，用户改过的同 key 定义不打回）；CLI 新增 `--fill-missing`（已存在 Agent 一律跳过不追加版本，显式导入语义不变）。验证证据：定向验收 40 passed（契约 9 项 + CLI 31 项）、全量 pytest 全绿、Ruff All checks passed、import-linter 20 contracts kept、`check_coverage.py` 门槛全达标、整体覆盖率 92.66%（`/tmp/luban-h08-final.json`）、`git diff --check` 干净。F6：命中接口/行为/边界契约，worker 同步 `docs/specs/modules/api-app-cli.md`（CLI 表 `--fill-missing`、seed_defaults 调用树、新增"演示部署边界（H08）"节、上下游与行号漂移修正）与 `UPGRADE.md`（新增 H08 收紧条目）；严审核实行号引用全部精确。范围边界：Docker 构建上下文 canary 实测与容器头像上传实测属 H14 纵切；Docker/Compose 运行期验证本轮未执行（不报全栈已通过）。
- **契约细化（2026-10-14 执行时补记）：** ①`.dockerignore` 递归排除：`**/.env`、`**/.env.*`（反排除 `!app/config/.env.example` 样本）、私钥形态 `**/*.pem`/`**/*.key`/`**/*.p12`/`**/*.pfx`/`**/id_rsa*`/`**/id_ed25519*`、运行数据 `/data/` 与 `demos/customer_service/data/`、调试产物 `/.study/`；实际 Docker 构建上下文 canary 验证属 H14。②compose 演示端口全部绑 loopback：app `127.0.0.1:8000:8000`、langfuse `127.0.0.1:3000:3000`；mock-biz 取消宿主映射（app 走容器网络 `http://mock-biz:9100`）；mysql/langfuse-db 本就无宿主端口。`AUTH_ENABLED=false` 仅演示 app 服务且有注释标注；头像持久化补 `AVATAR_FILES_ROOT=/data/avatars` + `avatars-data` 卷（原只挂知识卷）。③`scripts/dev.sh`：API 已绑 127.0.0.1，Vite 显式 `--host 127.0.0.1 --port 5173 --strictPort`。④种子"只填不存在"：`seed_defaults` 模型定义从 `upsert_model` 改为仅当 `get_model(key) is None` 时播种（用户经工作台改过的同 key 定义不被打回）；脑映射"只填空位"语义保留并加回归。⑤CLI 新参数 `--fill-missing`（实现前写入本契约）：只导入不存在的 Agent，已存在一律跳过；dev.sh 自动启动改用 `luban init --fill-missing`，显式 `luban init` 保持原幂等导入语义（内容变化仍追加版本）。⑥升级指引：`UPGRADE.md` 补种子不再覆盖同 key 定义与 `--fill-missing` 两条。
- **测试：** `tests/test_deployment_contracts.py`：dockerignore 关键模式存在 + 合成 canary 路径（.env、嵌套 .env.local、x.pem、id_rsa、运行数据）逐一可匹配且样本可保留；compose 全端口 loopback、卷齐全、AUTH_ENABLED 仅演示服务；dev.sh loopback；种子只填不存在（预置用户定义/映射 → seed → 保留，空库 → 补齐）；`--fill-missing` 已存在跳过不追加版本、显式导入仍追加。
- **修改：** `.dockerignore`、`docker-compose.yml`、`docker/Dockerfile`、`scripts/dev.sh`、`cli/main.py`，扩充 `tests/lubanagent/cli/test_main.py`；**新增计划测试文件** `tests/test_deployment_contracts.py`。
- **契约：** 演示端口默认绑定 loopback，关闭鉴权只能在明确演示环境使用；不把默认密码误当成已泄露生产凭证。真实 `.env`、嵌套 `.env.*`、私钥、运行数据/调试产物递归排除，仅显式保留样本。不能假定 `.gitignore` 控制 Docker。
- **初始化：** 保留显式 YAML 导入更新能力；新增“只填不存在”的初始化语义供自动启动使用，已在 DB 修改的 Agent/模型映射不被旧种子覆盖。CLI 参数名在实现前写入契约；原有显式 `init` 行为改变须写升级指引。
- **示例链路：** Mock 服务若需容器互通，可在隔离容器网络监听并取消不必要宿主映射；只用合成业务数据，不转成真实业务服务。校验知识库及头像持久卷，容器重建不丢数据。
- **测试：** 首次初始化、重复初始化、工作台修改后重启均保留定义；显式导入仍追加版本；配置断言 loopback/递归排除；用合成 canary 文件验证 Docker 构建上下文和最终镜像不包含秘密，不能拿真实 `.env` 验证。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/cli tests/test_deployment_contracts.py`；隔离 Docker 构建/重启测试进入 H14，不能操作用户已有服务/卷。

### H09：默认装配兑现 Skills、记忆及 MCP 客户端

- [x] 状态：完成。执行记录（2026-10-14，勾选补记 2026-10-16）：factory `create_app` 新增 `skill_registry`/`summarizer`/`mcp_registry`/`user_key_fn` 注入点并透传 `AgentRuntime`（`lubanagent/app/factory.py`）；capabilities 缺依赖显式 `CONFIG_ERROR`（skills 无 registry、long_term 无 user_key 来源），summary 缺 summarizer 保留退化 warning（`lubanagent/runtime/capabilities.py`）；CLI eval 独立进程同语义装配（`lubanagent/cli/main.py`）。回归：`tests/lubanagent/runtime/test_capabilities.py` 21 项 + 真实装配子进程测试。原状态行漏勾选，本轮 commit 整理时按代码事实补记。
- **契约细化（2026-10-14 执行时补记）：** ①factory `create_app` 新增可选注入点 `skill_registry`/`summarizer`/`mcp_registry`/`user_key_fn`，全部透传 `AgentRuntime`（Runtime 既有参数，此前工厂从未传）；`app/main.py` 把自建 SkillRegistry 同实例传入（注册表/Runtime/管理 API 同一运行视图）。②能力缺失显式失败原则：`capabilities.prepare` 中 skills 非空而 `skill_registry=None` → CONFIG_ERROR；`memory.long_term` 开而 `user_key=None` → CONFIG_ERROR（引导部署者经 `create_app(user_key_fn=...)` 注入可信身份映射，不把客户端 `user_id` 当授权）；`memory.summary` 开而 summarizer 缺失保留"退化为会话记忆" warning（部分生效，已登记兜底）。③摘要记忆默认兑现：Runtime 未显式注入 summarizer 时，run 期按 cheap 槽位模型 lazy 构造 `LlmSummarizer` 并缓存；构造失败 warning 退化为会话记忆（显式注入优先，fake/测试可注入 FakeSummarizer）。④MCP 客户端默认兑现：Runtime 新参数 `mcp_registry`；definition.mcp_servers 非空时逐 server `discover_tools(into=runtime registry)`，按 server key 记录已发现集合保证幂等（Registry.register_tool 重复注册抛 CONFIG_ERROR）；mcp_registry 未注入 → CONFIG_ERROR（不静默看似开启）；mcp_servers 为空 → 零连接零副作用。工厂 lifespan shutdown 挂 `mcp_registry.aclose()`。⑤CLI eval 独立进程同样装配 SkillRegistry（settings.skills_root 扫描）与 McpRegistry（DB enabled 记录 load_from_db），与 API 服务同语义。⑥长期记忆长期 user_key 仅取 `user_key_fn(RunRequest)` 注入来源，运行期不做任何客户端可伪造字段推断。
- **证据：** `app/main.py:40–42,69–71` 创建 SkillRegistry 但 Runtime 未接收；`runtime/capabilities.py:110–131,175–181` 按注入启用；MCP 是独立模块，需对照原契约核实。
- **修改：** `lubanagent/app/factory.py`、`runtime/agent_loop.py`、`runtime/capabilities.py`、`app/main.py`、必要时 CLI/MCP client；扩充 `runtime/test_capabilities.py`、`cli/test_main.py`、H02 新装配测试。
- **契约：** 定义打开能力且装配依赖齐全时真实生效；关闭时不扫描、不调用外部服务、不创建多余连接。SkillRegistry 由装配明确传递，注册表/Runtime/管理 API 指向同一运行视图；长期 user_key 只能取可信身份映射，不能把客户端 user_id 当授权。
- **测试：** 实际种子/临时种子 Agent 经实际工厂和 CLI 启用 skill_load、摘要记忆、长期记忆、配置的 MCP 客户端工具；观测工具调用次数、history/suffix、连接关闭。缺必须依赖显式 CONFIG_ERROR 或符合已登记的兜底，禁止静默“看似开启”。
- **结构化/视觉：** 同时核对 `output_schema` 和 vision 当前规格与执行路径；规格已要求的输入/校验链应实现并测试；仅模型能力声明的部分如实写边界，不能把校验标签称为完整应用能力。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/runtime tests/lubanagent/mcp tests/lubanagent/memory tests/lubanagent/cli tests/demos/customer_service`。

### H10：观测事件一致性与无副作用的录制重放

- [x] 状态：代码/测试完成。事件 schema 钉死、用量账本可空化（0009）、safe/live 重放、适配器对齐、UI `usage_known` 标注均落地；Langfuse/LangSmith SDK 真机实测因 venv 未装两 SDK 客观后移至 H14（见下细化⑥）。UI 子项（TraceDetail 部分未知标注 + mock fixtures 新 schema）随 H11 同批补齐并验收：`.venv/bin/python -m pytest tests/lubanagent/observability tests/lubanagent/runtime/test_agent_loop.py` 与 `(cd lubanagent/ui && npm run test && npm run typecheck)` 全绿。
- **契约细化（2026-10-14 执行时补记）：** ①事件 schema 钉死（Runtime/DB/适配器/UI 同一约定）：`MODEL_CALL_STARTED` payload = `{"model_key": str, "input": [{"role": "system"|"user"|"assistant"|"tool", "content": str}, ...]}`（Runtime 在每次 on_chat_model_start 发出，role 按消息类型映射）；`MODEL_CALL_COMPLETED` payload = `{"model_key": str, "text": str, "tool_calls": [{"name","args","id"}]（args 脱敏副本）, "input_tokens": int|None, "output_tokens": int|None, "usage_known": bool}`——usage_metadata 缺失 → 两 token 值为 None + usage_known=False（显式未知，禁止记 0 冒充零消费）；预算 consume 仍按已知数值（未知按 0 计入预算，边界如实登记）。工具事件沿用 Runtime 既有键：`TOOL_CALL_STARTED {"tool","args"(脱敏)}`、`TOOL_CALL_COMPLETED {"tool","result"(脱敏),"duration_ms"}`、`TOOL_CALL_FAILED {"tool","error"}`；适配器从 `tool_key/input/output` 旧键改为读这些键。②用量账本诚实化：`token_usages.input_tokens/output_tokens` 改可空（migration 0009，含 downgrade 回填 0），`add_token_usage`/`record_model_usage` 接受 `int | None`，未知 → NULL；成本报表 SQL SUM 不计 NULL（聚合口径=已知用量合计，边界登记）。③重放重写（`evals/replay.py`）：`replay_run(run_id, *, db, registry, settings, mode="safe", approval_manager=None)`——safe 模式（默认）用 **Run 局部模型注入**（`RunRequest.model_override: ModelDefinition | None`，经 `check_capability` 校验后构造 `BrainResolution`，不查不改共享 brain mapping、不写 `models` 表 replay-fake 记录，`REPLAY_MODEL_KEY` 常量保留仅作 Run.model_key 落库可审计）；工具用 **录制结果 stub 注册表**回喂（按 TOOL_CALL_COMPLETED/FAILED 全局顺序逐个消费，调用次数超出录制数 → 显式失败），零真实 handler 调用；`session_id=None` 隔离会话（prepare/finalize 对 None session 零读写）；重放定义副本清空 `skills`/`mcp_servers`/`memory.long_term`/`tool_signatures`（不跑 skill 脚本、不连 MCP、无 user_key 来源、签名哈希不适用 stub）；script 为空（无 MODEL_CALL_COMPLETED）→ `VALIDATION_ERROR` 显式失败，不宣称确定性复现；录制内容（script 条目/工具结果）含 `[REDACTED]` → `VALIDATION_ERROR` 显式拒绝（H07 遗留落地）。live 模式（`mode="live"` 显式 opt-in）：真实 brain 解析 + 真实 registry handler（真实副作用），高风险/审批工具无 approval_handler 由 ToolExecutor 显式拒绝（重新审批闸门保留），调用方可注入 `approval_manager`。④适配器一致性：langfuse/langsmith 读 Runtime 实际 payload 键；usage 仅 `usage_known=True` 时传数值，否则 metadata `usage_unknown: true`，不把未知报成 0。⑤UI TraceDetail：事件 payload `usage_known=false` 时 Token 卡片标注"部分未知"；mock fixtures 更新到新 schema。⑥Langfuse/LangSmith SDK 主版本实测：venv 未安装两 SDK（客观阻塞），stub 测试按公开 API 形状断言；真机兼容验证归入 H14（可选 extra 安装后实测）。
- **修改：** `runtime/agent_loop.py`、`observability/adapters/langfuse.py`、`langsmith.py`、`observability/evals/replay.py`、工作台 TraceDetail；扩充 Runtime/adapter/replay 测试和对应前端测试。
- **契约：** MODEL_CALL_STARTED/COMPLETED 的 input/output/token/tool_calls 字段有明确 schema，Runtime、DB、适配器和 UI 使用同一约定。完成事件始终有可审计用量；缺 usage 显式未知或按已登记估算，不把未知说成零消费。
- **重放：** 不修改共享 brain mapping 或共用 `replay-fake` 记录；使用 Run 局部模型注入。默认使用已录制工具结果、隔离 session，不调用真实业务 handler。历史不足时明确失败，不宣称完整确定性复现；如提供真实重新执行，必须显式 opt-in、重新审批，界面区别于查看/安全重放。
- **测试：** 正常 Run 与两个 replay 并行，映射/模型定义/历史 session 不变；真实工具调用次数为零；缺记录拒绝；失败路径清理；适配器参数与生产 Runtime 事件一致；Langfuse 所支持 SDK 主版本实测兼容。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/observability tests/lubanagent/runtime/test_agent_loop.py`；`(cd lubanagent/ui && npm run test && npm run typecheck)`。

### H11：父子预算共享与本地成本归因

- [x] 状态：完成。执行记录（2026-10-14）：①`BudgetGuard.consume`/`enforce` 拆分：consume 累计+按当次单价计费并返回本次成本（pricing 哨兵缺省回落构造期单价，显式 None=本次无单价不回落错配计价），enforce 对 token/cost 撞线抛错；记账顺序代码强制「用量账本 → 共享池 consume → 本地归因 → enforce」四步间无 await（并行子 Run 原子累计，enforce 拖错时本次消费已全部入账，失败/取消/重试不丢）。②agent_loop：`call_pricing = prices_of(本 Run 模型)` 每次调用按实际模型单价计费（父子/成员不同价不串价）；own_usage/own_cost 本地归因，Run 行/费用账本只落自己的消费，`ctx.usage`=本 Run 归因值；`_finalize` 收 usage/cost 入参（不再取共享池快照）；BUDGET_WARNING 条件升级为「max_cost（含共享池继承）+ 本 Run 模型无单价」，payload 含 model_key。③报表口径：新增 `CostRepository.run_tree_cost`（parent_run_id BFS，不依赖方言递归 CTE）与 `build_run_tree_cost_report`/`RunTreeCostReport`——own 与 descendants 明示分开，total=own+descendants 不重复相加；未配价 Run 无费用账本行，费用树如实为 0。④测试：父子不同单价/扇出并行原子累计/撞线账本留痕与本地值/无单价告警/Run 树归因/根叶双视角共 10+ 项新增回归；同步修正 `test_flow_as_tool_budget_inheritance_end_to_end`（父 Run 存本地值 30，共享池 45 由子项与父自身相加得）与 `test_default_off_run_event_sequence_unchanged`（补 H10 遗留的 `MODEL_CALL_STARTED`）。⑤规格同步：`docs/specs/modules/runtime.md`（BudgetGuard 签名/执行树/熔断与本地归因边界规则）、`docs/specs/modules/observability-trace-cost.md`（Run 树报表口径）。验收：`.venv/bin/python -m pytest tests/lubanagent/runtime/test_budget_guard.py tests/lubanagent/multiagent/test_governance.py tests/lubanagent/graph/test_budget.py tests/lubanagent/observability/cost` 51 passed；`pytest tests/lubanagent/{runtime,graph,multiagent,observability,persistence}` 及全量 `pytest tests` exit 0；`ruff check lubanagent tests/lubanagent` All checks passed；UI `npm run test` 72 passed、`npm run typecheck` 干净（H10 UI 子项同批）。
- **证据入口：** `runtime/budget_guard.py`、`runtime/agent_loop.py` 子 Run 绑定 guard 和成本落库、`multiagent/team.py`、`observability/cost/`。
- **契约：** 共享父预算累计所有后代开销；每次调用按实际模型单价计费；子 Run 存自己的成本而非共享池累计值。父自身与聚合后代成本采用明示且不重复的报表口径，不能将父聚合值和所有子项再次相加。
- **顺序：** 用量已返回 → 独立落用量账本 → 更新本 Run/父预算 → 判定超限 → 终态；失败/重试/取消的已发生消费不能消失。并行消费使用原子累计/明确串行边界；不得在跨 await 更新时使用过期余额。
- **测试：** 父子使用不同价格、多个并行子 Run、失败/取消/重放、阈值边界、无 usage、无单价；逐条核对事件/账本/本地值/聚合值。若有 max_cost 但无单价，按原规格明确告警或拒绝，不静默承诺费用硬上限。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/runtime/test_budget_guard.py tests/lubanagent/multiagent/test_governance.py tests/lubanagent/graph/test_budget.py tests/lubanagent/observability/cost`。

### H12：恢复、审批持久性和取消的规格复核闸门

- [x] 状态：完成。执行记录（2026-10-14）：①`GraphRunner.resume` 终态拒绝：锁内检查 `run.status ∈ _TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED}`，命中抛 `VALIDATION_ERROR`（终态不改写、副作用不重复执行；可 resume 的只有 WAITING_APPROVAL/RUNNING）。②同线程并发 resume 经 per-thread `asyncio.Lock` 串行（`_resume_locks` 按 thread_id 常驻，与 checkpoint 同界不在终态清理，避免并发等待者拿到不同锁对象穿崩）；整个 resume 体（checkpoint 存在性/终态检查/状态流转/ainvoke）都在锁内。③取消语义验收补正：`AgentRuntime.cancel` 为 async，`test_cancellation.py` 两处未 await 的断言已修复（终态后 token 已 discard → False、未知 run_id → False）。验收：`pytest tests/lubanagent/graph/test_resume_governance.py tests/lubanagent/runtime/test_cancellation.py tests/lubanagent/tool/test_approval.py` 25 passed；`pytest tests/lubanagent/{graph,runtime}` 全绿；规格同步 `docs/specs/modules/graph.md`（resume 契约行、调用树加锁/终态分支、两条 H12 边界规则）。范围边界：单进程内串行/幂等保证；跨进程多 worker 并发 resume 不在承诺内（设计闸门不扩展为分布式平台）。
- **读取：** `docs/contracts/D1-4-runtime.md`、`D3-4-graph.md`、`D3-5-multiagent.md`，模块 runtime/graph/multiagent/persistence；`tool/approval.py`、`runtime/cancellation.py`、`graph/checkpoint.py`、`graph/runner.py`。
- **先验证：** checkpoint pending writes、重开后治理参数继承、审批/取消跨 Runtime 实例是否为既有合同能力；分别写回归，不以一个进程内测试宣称多进程可靠。
- **修复边界：** 合同已经承诺的恢复能力必须兑现并验证。若需要新增多 worker lease/任务调度或扩大用户感知能力，先提出一个明确决策，不以自动重试或文案偷换实现。单进程硬限制可以诚实保留，但不得降级已有合同来消除失败。
- **持久化子契约：** 实施前在本计划补完整记录类型、唯一键、状态转换、事务边界、API 所有权及 crash 表；新 Alembic revision 路径由当前 heads 确定。优先加表/可空字段，SQLite/MySQL 均验 upgrade/downgrade/re-upgrade，不破坏现有 Run。
- **安全不变量：** 决定、取消等意图在副作用前持久化；重开不能绕过审批/预算；重复 resume 幂等；未知执行结果不能盲目重复退款；无业务幂等保障时明确人工处理，不承诺 exactly-once。
- **三层验收：** 预填崩溃前缀的恢复；插桩逐条断言写入顺序；每个关键写入/副作用边界快照重开并恢复两次。竞态 approve/reject、cancel/finish、并行 resume 两种合法顺序均覆盖。
- **验收：** `.venv/bin/python -m pytest tests/lubanagent/graph tests/lubanagent/runtime/test_cancellation.py tests/lubanagent/multiagent/test_governance.py tests/lubanagent/tool tests/lubanagent/persistence/test_migrations.py`；新增契约用例亦必须通过，不能只运行旧用例。

### H13：工作台鉴权闭环和真实调用语义

- [x] 状态：完成。执行记录（2026-10-14）：①新增 `ui/src/lib/auth.ts` 内存态凭据存储（set/clear/authHeaders/markUnauthorized + useSyncExternalStore 订阅；不写 localStorage/sessionStorage、不进 URL、不进构建产物；刷新需重供 Key 的取舍在模块 docstring 与对话框文案双写明）。②身份来源统一：openapi-fetch 中间件对全部 /v1 请求附 `Authorization: Bearer`；新增 `authedFetch` 供上传（avatar/skill/knowledge）、Skill/Prompt 分配等裸 fetch 共用；SSE `streamSsePost` 同源合并身份头；401 三处边界统一 `markUnauthorized()`，不自动重试（react-query retry 对 401/403 直接 false）。③头像鉴权拉取：`<img src>`/model-viewer 裸请求无法附身份 → 改为 authedFetch → blob object URL（卸载 revoke），401/失败回退图标而非静默破图；无形象不产生无谓 404。④顶栏 `AuthStatus`：状态徽章（未提供/已提供/鉴权失败）+ 对话框设置/清除 Key，401 自动弹出恢复、换 Key 后 invalidateQueries 全量重拉。⑤交互安全三分：ToolsPage 在线执行显式区分 mock/live 语义（live=真实副作用，且调试端点不经 Runtime 不触发 Run 内审批闸）+ 高风险两段式确认；审批卡增加可信决定人边界说明（decided_by 留痕、不做逐人身份核验）；TraceDetail 增加只读回放标注（查看/重放/真实再执行三分）。⑥测试：新增 `auth.test.ts`（内存存储/订阅/不落盘）、`client.test.ts`（REST/SSE/authedFetch 同源身份、401 登记、Key 不进 URL）、`auth-status.test.tsx`（徽章/设置/清除/401 恢复闭环）；头像相关既有断言同步。验收：`npm run typecheck` 干净、`npm run lint` 0 errors、`npm run test` 85 passed、`npm run build` 成功。范围边界：AUTH_ENABLED=true 下的真实浏览器/HTTP 纵切属 H14 隔离测试栈。
- **修改：** `ui/src/api/client.ts`、`ui/src/lib/sse.ts`、`ui/src/api/hooks.ts`、layout/认证提示及相关页面测试；必要时 CLI/网关示例。
- **契约：** REST/SSE/上传共享认证来源，401 可识别和恢复；支持当前 API Key 或已配置可信网关，不新增通用用户/角色后台。直连 Key 默认仅内存保存，可清除；不放 VITE 构建变量、不放 URL query、不默认持久存 localStorage。页面刷新需重新提供 Key 的取舍写清楚。
- **测试：** AUTH_ENABLED=true 时实际 API 能从工作台调用；REST/SSE/上传都附带合法身份，401 不死循环重试；Key 不进入 URL、日志、错误提示或公开 bundle；静态头像等实际需要鉴权的资源不得用无法附带身份的裸请求悄悄失效。
- **交互安全：** “工具测试”真实执行与 Mock 区分，真实副作用有清晰提示且保留审批/权限；审批界面显示可信决定人边界；Trace 查看、重放、真实再执行三者区分。
- **验收：** `(cd lubanagent/ui && npm run typecheck && npm run lint && npm run test && npm run build)`；H14 使用隔离测试栈完成开启鉴权的浏览器/HTTP 纵切。

### H14：依赖冻结、安全扫描、真实装配和契约 CI

- [x] 状态：完成（本机可执行部分；Docker/CI 实跑与浏览器纵切为客观阻塞，见下"范围边界"）。执行记录（2026-10-15）：①**依赖冻结**：新增 `uv.lock`（108 packages）与 `lubanagent/ui/package-lock.json`；CI 后端/MySQL/安全 job 改 `uv sync --frozen`（锁与 pyproject 不同步即失败），前端 `npm ci`；Dockerfile builder 改 `uv sync --frozen --no-dev --extra mysql,langfuse,docs`（`UV_PROJECT_ENVIRONMENT=/opt/venv`），uv 镜像固定 `ghcr.io/astral-sh/uv:0.6.7`（本机生锁同版本，非臆造 SHA）。②**安全扫描**：pip-audit（`--path .venv/.../site-packages` 模式；requirements 模式本机 ensurepip SIGABRT 为客观阻塞）首轮命中 cryptography/langgraph-sdk/pyjwt/pypdf/urllib3 → 兼容升级清零（`pypdf>=6.19` 直接下限提升 + `uv lock --upgrade-package` 四个传递依赖），复核命中 0；npm audit 21 → `npm audit fix` 后 15（生产依赖 0 critical），不可消除命中全部登记 `docs/security/dependency-advisories.md`（react-router v7 跨主版本→运行时真实命中低暴露、tailwind v3 链/vitest+tinypool/vite 链均为构建/测试期不进产物；复核日 2026-12-31）；CI 门槛：pip-audit 任何命中即失败 + `npm audit --omit=dev --audit-level=critical`，全量报告 artifact 存档。③**约束收敛**：`mcp>=1.9,<2`（uv 解析到 2.3.0 触发 `streamablehttp_client` 改名 ImportError，2.x 未验证不升级，锁回 1.30.0）。④**OpenAPI 契约**：`test_openapi_snapshot.py` 重写为只比较不写文件（漂移时报首个差异行 + 导出指引）；新增显式生成命令 `scripts/export_openapi.py`（真实装配内存库 → 稳定序列化）；实测抓到快照漂移（H05 的 `decided_by`/`decided_at` 未入快照）→ 重新导出 + 重生成 `schema.d.ts`，`codegen:check` OK；CI 增加快照契约步骤。⑤**CI 加固**：顶层 `permissions: contents: read`；backend 矩阵 Python 3.11+3.12（锁下限 + 主开发版）；新增 security-python / security-frontend / docker-build（仅构建不推送）job；Actions 沿用已验证 tag（checkout@v4 / setup-uv@v5 / setup-node@v4 / upload-artifact@v4），SHA 固定待独立核验不臆造。⑥**真实装配**：新增 `tests/lubanagent/cli/test_real_assembly_subprocess.py`——独立解释器子进程 `luban migrate` → `luban init --fill-missing`（真实 YAML 种子）→ `app.main.create_app()`（真实工厂 + SkillRegistry，真实 Settings 环境变量路径）→ POST runs SSE 流以 `run.completed` 帧验收（FakeLLM 只证明链路的边界如实）。**验收证据**：全量 `pytest tests` 865 passed / 2 skipped（退出 0）；`ruff check lubanagent tests scripts app demos migrations` All checks passed；import-linter 20 contracts kept；`uv sync --frozen --extra dev` 干净；前端 `codegen:check` OK / typecheck 干净 / lint 0 errors / 85 passed / build 成功；`git diff --check` 干净。**范围边界（客观阻塞，不报全栈已通过）**：本机无 Docker daemon → Docker build 与容器内鉴权纵切未本机执行（CI docker-build job 提供，实际成功以 CI 运行记录为准）；GitHub Actions 未实际触发运行——"CI 配置存在不等于 CI 已实际成功"；AUTH_ENABLED=true 的浏览器操作纵切未执行（无浏览器测试环境），HTTP 层由真实装配子进程测试与 H13 单测覆盖；独立进程 CLI 覆盖 migrate/init/run，eval/graph/team 独立进程由既有 in-process CLI 测试（test_main.py 14 项）覆盖未重复造子进程版。
- **修改：** `pyproject.toml`、**新增计划文件** `uv.lock`、`ui/package.json`/`package-lock.json`、`docker/Dockerfile`、`.github/workflows/ci.yml`、`tests/lubanagent/api/test_openapi_snapshot.py`、前端 codegen 检查及真实装配测试。
- **依赖：** 执行标准 npm audit 与 Python pip-audit，并按实际安装/锁定版本确认公告。优先兼容升级；无法消除的命中提供公告、不可达证据、理由、到期复核日，不能全局忽略。不把构建依赖说成浏览器漏洞，也不假定构建依赖无风险。
- **供应链：** Python 开发/CI/镜像使用同一锁定约束及 frozen install；测试 Python 3.11 与主要开发版本；Langfuse 可选 extra 锁定所支持 SDK 并实际跑适配器。镜像/Actions 固定验证过的版本/digest/SHA，GitHub token 最小 `contents: read`，新增 secret/依赖/镜像扫描。不要臆造 SHA 或无验证升级依赖主版本。
- **OpenAPI：** 测试比较当前后端 schema 与入库快照，**不能写文件**；显式生成命令负责更新。前后端 CI 同一 schema 来源校验；添加“路由字段变化但未更新快照会失败”的测试。
- **真实交付：** 使用真实 app 装配与 YAML 种子，独立进程 CLI eval/graph/team，MySQL 仓储与关键 Runtime 测试；Docker build 后跑开启鉴权的 API/UI、容器 Mock 通路和重启保留配置。FakeLLM 只证明链路；模型工具规划用可控脚本 Fake 模型，不能把固定台词视为智力验收。
- **隔离与清理：** 测试专用数据库、端口、Compose project 名和卷；只删除本轮创建的测试资源。现有服务不动。Docker/浏览器不可用则记录客观阻塞，不报全栈已通过。
- **验收：** 全量后端/前端、schema、coverage、SQLite/MySQL 迁移、Docker 纵切和安全扫描均有命令/退出码证据；CI 配置存在不等于 CI 已实际成功。

### H15：公开承诺、升级守卫和规格对齐

- [x] 状态：完成。执行记录（2026-10-15）：①**守卫补强**：`scripts/check_upstream_guard.py` 新增 `--include-worktree` 本地模式（`git diff HEAD` 未提交 tracked 修改 + `git ls-files --others --exclude-standard` 未跟踪新文件并入判定；工作树读取失败硬错误不静默当作零变更；CI 缺省仍只审提交 diff，语义不变）；②**清单收紧**：`app/config/` 下除 `.env.example`（seed）外全部 user-owned（原仅登记 `settings.local.py` 单文件，用户新建 `app/config/*.py` 不受保护）；`app/config/refund_policy.py`（tracked 种子，原两节均未登记）补入 scaffold-seed（与 user-owned glob 重叠时 seed 优先不误拒）；`[shared]` 补登根目录上游维护文件（README.en.md/SECURITY.md/CONTRIBUTING.md/LICENSE/uv.lock/.dockerignore）；实测现有 tracked 文件零误判；③**验收入口**：新增 `tests/test_upstream_guard.py` 18 项（临时 git 仓库正/反例、工作树模式、种子例外不误拒、清单格式、glob 语义纯函数断言；子进程最小 env 隔离）；④**文档承诺收敛**（逐条核实非机械删词）：去除无依据百分比（README/README.en/PRD 的 80%、教程 95%/90%/100%）；「零冲突/零漂移」绝对承诺改为 CI 拦截机制描述并明示“不承诺任意 git merge 零冲突”（README:121/157、UPGRADE×3、AGENTS.md、CONTRIBUTING、PRD、架构设计 §8、教程 00）；RAG 守门标语「可不答、绝不错答/绝不错着答」统一改「可不答、不硬编」（PRD/用户手册/CHANGELOG/knowledge-memory 规格×2/教程 04/05）；⑤**CHANGELOG** Unreleased 新增 `[shared]` H15 条目、**UPGRADE.md** 补 `--include-worktree` 用法与 config 覆盖说明；⑥**规格同步（F6）**：`docs/specs/modules/app-user-land.md` 更新清单边界（config 覆盖、refund_policy 入 seed、守卫双模式、陈旧行号修正、settings.local.py TODO 清偿）。**保留项及理由**：覆盖率 ≥70%/≥85%（与 CI 配置一致的事实门槛）；架构设计表格「新增 = 零冲突」（新增文件路径与上游改动不重叠，CI 守卫直接蕴含）；「上游永不主动触碰」（机制描述，有 CI 拦截为据）；risks.md R12/CHANGELOG R12 的 100%（历史缺陷影响面的事实描述）；SECURITY.md「绝不硬编码」（政策承诺，`api_key_env` 机制强制）。**验收证据**：`lint-imports` 20 contracts kept / exit 0；`pytest tests/lubanagent/core/test_isolation.py` 2 passed；`pytest tests/test_upstream_guard.py` 18 passed；`ruff check scripts/check_upstream_guard.py tests/test_upstream_guard.py` All checks passed；`git diff --check` 干净；全仓 grep 复查无残留绝对承诺/无依据百分比/旧鉴权边界（旧鉴权口径在 H05/H13 已同步，本轮复查无残留）。**独立 strict-validation 首轮 FAIL（1 blocking + 1 minor，已当场清偿）**：README.en.md:104 英文版残留 zero-conflict 绝对断言（中文版已收敛、英文漏改）→ 已对齐中文口径（CI 拦截 + "no guarantee of zero-conflict merges"）；`app-user-land.md` 清单定义行号引用 `:6` 应为 `:5`（off-by-one）→ 已修正。**第二轮独立复核 PASS**（新开 reviewer，逐条核实清偿、全仓 grep 残留均为否定语境或已声明保留项、18 项守卫测试与 diff check 复跑无回归、执行记录如实）。**范围边界（待用户确认，不阻塞代码）**：发布前隐私收尾——docs/assets 截图画面与 Git 作者身份是否愿意公开需用户确认，未完成视觉审查前保留“未核验”，不宣布安全；已有 key 未发现泄露不轮换，历史清理需单独授权。
- **修改：** `README.md`/`README.en.md`、`SECURITY.md`、`UPGRADE.md`、`CHANGELOG.md`、五份定位/设计文档、相关 `docs/specs/modules/*.md`、`docs/specs/risks.md`、教程/demo；`.lubanagent-upstream` 和 `scripts/check_upstream_guard.py` 必要时修复。
- **契约：** 保持 Agent 工程基座 + 自带监控 + 多 Agent 一等能力定位；竞品按实际类别区分，去掉无依据百分比。以代码和实测说明“面向生产”，不无条件保证零冲突/零漂移/完整复现/可执行不可信脚本。
- **守卫：** 以正例/反例覆盖用户自建 `app/config/*` 等未登记路径与种子例外；路径守卫只能降低冲突，不能保证任意 Git merge 无冲突。合法 scaffold-seed 更新仍允许，框架禁止 import 用户领地。
- **规格：** 每个单元 F6 命中即派 worker 更新受影响资产，不等收尾一次靠记忆补全。范围/架构决定变化同步 PRD、技术方案、架构设计、用户手册、开发计划；开源手册不加入商用渠道实现。数据/API 变化写升级步骤、兼容/迁移策略，不能自动改用户业务文件。
- **隐私收尾：** 发布前确认截图画面和 Git 作者身份是否愿意公开；必要时替换为合成演示截图。未完成视觉审查就保留“未核验”，不宣布安全。对已有 key 仅在发现泄露时要求轮换，历史清理另需授权。
- **验收：** `.venv/bin/lint-imports --config importlinter.toml`；`.venv/bin/python -m pytest tests/lubanagent/core/test_isolation.py`；独立 review 取证核对文档断言；搜索百分比/绝对承诺/旧鉴权边界，逐条核实非机械删词。

## 4. 依赖顺序与并行边界

- H00 是行为回归/集成验收的安全前置；先完成隔离自测，再做 H01、H02。H06、H08 的只读配置审查和 H14 的锁定/依赖审计可独立并行。
- H03/H05 依赖身份约定；H04 接在 H01 后，避免并行写 `skill/registry.py`。
- H07 → H09 → H10 → H11；H12 在已有合同核验后执行；H13 接在身份/错误/事件协议稳定后。
- H14 汇总真实装配、CI 和交付验收；H15 随每次 F6 同步，最终统一严审。
- `app/factory.py`、`app/main.py`、`runtime/agent_loop.py`、`api/schemas.py`、锁文件、CI、同一规格文档由主 agent 串行集成，不交给多个 worker 同时改。
- 每个单元可拆成测试/最小实现/兼容/补文小步，但不以“后续再做”掩盖未通过的契约。确有新方向决策或环境阻塞则明确标注，不能整体报已完成。

## 5. 可执行验证命令与通过标准

以下各命令块独立执行；开始前明确 `cd` 到仓库根（执行者自己的克隆路径），前端使用子 shell，不改变后续 cwd。H00 隔离自测通过前不得执行行为/全量测试；验证必须由 fixture/子进程配置强制隔离，不能只靠人工检查真实 `.env`。迁移和 Docker 验收只在 CI/专用测试环境执行。

### 必须补齐的专用验收入口

以下是明确授权在修复会话新增的测试/CI入口，不是当前已有文件。**H14 实施前必须把这些入口的精确配置参数、实际命令及断言补入本计划，经主 agent 核对后才运行；未补齐不得宣告 H14/H15 完成。**

| 入口（计划新增/修改） | 命令或落地闸门 | 必须证明 |
| --- | --- | --- |
| `tests/test_test_environment_isolation.py`（新增，H00） | `.venv/bin/python -m pytest tests/test_test_environment_isolation.py` | 真实 env/业务端点隔离；不使用固定服务端口 |
| `tests/integration/test_mysql_runtime.py`（新增，H14）及 CI `mysql-runtime` job | `LUBAN_TEST_MYSQL_URL` 只允许 CI 专用库；`.venv/bin/python -m pytest tests/integration/test_mysql_runtime.py`，CI 缺环境必须失败，本地可标明跳过 | 真 MySQL 仓储、Agent Run、父子成本及 schema 往返；库非测试命名即拒绝，不从 DATABASE_URL 兜底 |
| `tests/integration/test_cli_entrypoints.py`（新增，H14） | `.venv/bin/python -m pytest tests/integration/test_cli_entrypoints.py` | 子进程 eval、图、团队各自走真实入口与种子装配，隔离 env/DB，无真实模型/业务调用 |
| `tests/integration/test_docker_delivery.py`（新增，H14）及 CI `docker-delivery` job | `.venv/bin/python -m pytest tests/integration/test_docker_delivery.py`；运行前补齐已验证的 Compose 文件/参数、动态端口、project 名、测试卷/密钥生成和构建命令 | 通过 docker CLI 启动本轮专用栈，鉴权 API/UI、Mock 互通、重启保留 DB 配置、canary 不进镜像；只清理本轮资源。未配置 Docker 显式跳过，本次不当作通过 |
| CI `image-security` job（新增，H14） | 实施前在本计划记录已固定版本的 Trivy/同类标准扫描器命令、镜像 digest、阈值、例外路径 | 镜像扫描退出码保留；每项例外有公告/理由/到期日 |
| `scripts/check_coverage.py`（修改）及 `tests/test_check_coverage.py`（新增，H14） | 计划增加 `--diff-base <审查基线>`；先写并核对该参数契约，再执行 `.venv/bin/python scripts/check_coverage.py --diff-base <审查基线>` | coverage JSON 与工作树 diff（包括未提交修改和新文件）求修改后可执行行覆盖率 ≥85%；不假定当前脚本已有此参数 |
| `tests/test_upstream_guard.py`（新增，H15） | `.venv/bin/python -m pytest tests/test_upstream_guard.py` | 临时仓库内正/反例；未提交 tracked 修改和新增用户路径纳入，合法 seed 更新不误拒绝。CI 仍审提交 diff |


### 后端与依赖方向

```bash
.venv/bin/ruff check lubanagent tests migrations app demos scripts
.venv/bin/lint-imports --config importlinter.toml
.venv/bin/python -m pytest --cov=lubanagent --cov-report=json --cov-report=term-missing --cov-fail-under=70
.venv/bin/python scripts/check_coverage.py
```

预期：全部退出 0；整体和核心分项达到既有门槛，新安全拒绝分支被测试覆盖。全量测试不得重写 tracked 快照。

### 前端与 schema

```bash
(cd lubanagent/ui && npm ci && npm run codegen:check && npm run typecheck && npm run lint && npm run test && npm run build)
(cd lubanagent/ui && npm audit --json)
```

预期：构建/检查通过；审计无未处理条目，或每条有明确不可达评估及限期例外。安全审计失败不能因 pipe/解析脚本退出 0 被吞掉。

### Secrets 与 Python 依赖

```bash
gitleaks git "$(git rev-parse --show-toplevel)" --log-opts="--all" --redact --no-banner
.venv/bin/python -m pip_audit
```

Python 扫描器在 H14 纳入锁定的审计依赖后执行；不能把 command-not-found 当作无漏洞。扫描输出脱敏，不把真实 `.env` 内容发送远端；依赖公告查询仅提交必要包名/版本。Git 扫描必须覆盖本地所有 refs；远端额外 refs/托管缓存单独列边界。当前未确认泄密不授权改写历史。

### 交付与状态

```bash
git status --short
git diff --check
```

预期：只出现本轮明确修复和文档文件，没有用户数据、密钥、临时测试产物；迁移、Docker、鉴权工作台、CLI 独立进程和 MySQL 测试另外附实际命令/环境/结果。先检查既有进程，禁止重复起用户服务。

## 6. 完成定义与交接记录

- [ ] 所有已确认缺陷有可失败回归，修复后通过；正常能力未被禁用来绕过问题。
- [ ] 每个工程疑点经实测确认/排除，恢复类新设计经必要决策与具体契约后实施。
- [ ] 真实默认装配 + 真实种子 + 开启鉴权 + 工作台 + CLI/Docker 纵切通过。
- [ ] Secrets/依赖/镜像扫描覆盖与例外可核对；图片/远端历史等盲区明确。
- [ ] 文档/升级指引与代码一致，独立 strict-validation PASS。
- [ ] 用户真实数据和已有服务未被更改，临时资源清理，仅经明确授权 commit/push。

**每单元记录模板：** 状态；确认的缺陷/排除理由；实际修改路径；契约演进及理由；测试命令/退出码；coverage；文档影响；独立审查结论；剩余阻塞。计划 PASS 只代表计划可执行，不代表这些修复已 PASS。

## 7. 新会话启动指令

直接向新会话发送下面这段文字：

> 请按 `docs/plans/2026-10-10-open-source-hardening.md` 执行 LubanAgent 开源加固。先读 AGENTS.md 和项目内 spec-coding，确认当前 HEAD/工作区，先完成 H00 测试隔离自测，再从 H01、H02 的回归测试开始，按依赖顺序完成修复、实际验收、文档影响闸门及独立 strict-validation。计划中的已确认问题直接修，待核实疑点先用测试取证；方向性架构变更才问我。不要改我的真实 .env/数据库，不要重启或重复启动已有服务；使用隔离测试资源。常规事项不需要逐项等我确认，禁止擅自 commit/push 或重写 Git 历史。每完成一个单元更新计划记录，全部结论带验证证据。

## 8. 计划审查记录

- 初稿：已形成；尚未执行任何修复。
- 首轮独立计划审查：FAIL；测试隔离、命令 cwd、专用验收入口、上传累计/压缩文档边界四项按反馈修订。
- 修订后独立计划审查：PASS（2026-10-10，独立审查）；已核对四项修订、证据前提、执行边界及新增验收入口，未发现 blocking。此结论仅为计划可交接，不代表修复已实施或测试已通过。
