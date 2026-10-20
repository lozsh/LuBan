# 资产目录同步指南（code → docs/specs）

本指南覆盖把任务目录（`.spec-coding/changes/<change-id>/`）产出或代码现状，同步进资产目录 `docs/specs/` 的取证与填充规则。**语言无关**：取证命令按目标仓库实际语言选择对应分支。

## 资产目录结构回顾

```
docs/specs/
├── index.md            # 系统一句话定位 + 模块导航表
├── glossary.md          # 跨模块术语（只放两个以上模块都要用的概念）
├── risks.md             # 跨模块长期风险登记 + 复盘（不放一次性排查过程）
└── modules/
    └── <module>.md      # 单模块规格全貌：定位/接口/数据/行为/边界，一个文件读完一个模块
```

四个文件类型职责不交叉：`modules/<module>.md` 回答"这个模块是什么、对外契约是什么、内部怎么转、有什么硬约束"；`glossary.md` 只收跨模块概念；`risks.md` 只收长期有效的风险与复盘；`index.md` 只做导航，不承载正文知识。

## 通用原则

- **同步来源**：由 §F6 触发时，以任务目录 `contract.md` + 本轮实际代码改动为准；由 R0' 独立触发（无任务目录）时，仅扫描代码现状。业务语义/参考文档由用户提供文本或链接时，与代码结合，无参考文档则仅代码。
- **只写有据可查**：每项填充须指向具体代码文件路径/行号，或 `contract.md`/参考文档明确表述。找不到保留 `[TODO: ...]` 并注明原因。
- **禁止空泛话术**：一句话去掉后信息量不变，就不要写。
- **增量而非全量**：模块文件已存在时，只更新本轮命中的章节，不做无关的整篇重写。
- **章节严格锁定**：各文件章节以 `assets/` 对应模板顶部 `TEMPLATE-LOCK` 注释为准。只允许填充模板内已有章节，禁止新增/改名/调序/加"常见问题"等模板外章节。
- **标注惯例**：`[已从代码提取]` 有明确代码证据；`[待人工核查]` 从命名/结构推断，可能不准；`[TODO: ...]` 找不到证据，需人工补充。

## 语言探测

```bash
ls pyproject.toml setup.py requirements.txt 2>/dev/null   # Python
ls package.json pnpm-lock.yaml yarn.lock 2>/dev/null        # Node
ls pom.xml build.gradle build.gradle.kts 2>/dev/null        # JVM
ls go.mod 2>/dev/null                                        # Go
ls Cargo.toml 2>/dev/null                                    # Rust
```

后续取证命令按探测结果选择对应分支；多语言仓库按各模块实际语言分别取证。

---

## index.md

**首次初始化**：一句话系统定位（从 README/包名/`contract.md` 综合，不可靠则 `[TODO]`）+ 模块导航表（模块名 / 一句话职责 / 文档路径）。

**增量更新**：本轮新增/删除模块时同步加行/删行；系统定位一般不因单次任务变化，除非任务本身改变了系统边界。

```markdown
| 模块 | 职责 | 文档 |
|---|---|---|
| [模块名] | [一句话职责] | `modules/[module].md` |
```

---

## glossary.md

**判断标准**：某概念只在一个模块内使用 → 写入该模块文件的"名词"节；在两个及以上模块中都需要理解 → 写入 `glossary.md`。

**填充逻辑**：本轮 `contract.md` 或代码改动引入的新概念，先判断是否跨模块；跨模块才写入，逐条写：术语（业务口语化叫法）+ 当前语义（完整含义、适用边界和场景）。

**空泛话术禁止**：不写"[术语] 是一种 [泛指机制]"，写"[术语]：[具体的角色/条件/范围/触发时机]，例如 [角色A] 只能看到 [具体限制范围] 内的数据"。

---

## risks.md

**取证来源**：代码信号（必须）+ 用户提供的已知问题/历史故障（有则结合）。

```bash
# 缺失异常处理 / 无重试的外部调用信号
grep -rE "except\s*:|except Exception" --include=*.py . 2>/dev/null | head -10
grep -rE "catch\s*\(\s*Exception" --include=*.java . 2>/dev/null | head -10
# TODO/FIXME/HACK 标记的技术债
grep -rEn "TODO|FIXME|HACK|XXX" --include=*.py --include=*.ts --include=*.java --include=*.go . 2>/dev/null | grep -v test | head -20
# 硬编码密钥/魔法值信号（只报位置，不抄具体值）
grep -rEln "password\s*=\s*[\"']|secret\s*=\s*[\"']|api_key\s*=\s*[\"']" --include=*.py --include=*.ts --include=*.java . 2>/dev/null | head -10
```

**候选风险条件**：有明确代码位置 + 能说清具体影响（不是"可能有问题"这种猜测）+ **长期有效**（不是本轮任务内已修复的问题）。

**填充约定**：每条风险写编号、具体描述、影响范围、代码位置来源、状态（待处理/已缓解/已接受，无法判断则留 `[TODO: 待人工评估状态]`）。复盘记录（真实故障事件）附在风险条目下方或单独一节，只在有真实事件材料时写，不可编造。

**降级处理**：代码信号扫描不出明显风险是正常情况，不为凑数把普通实现细节包装成"风险"；此时保留模板 `[TODO: ...]` 骨架并注明"首次初始化未发现代码侧明显风险，请在后续开发中按需补充"。

**边界**：一次性排查过程写进任务目录 `tasks.md`，不写进 `risks.md`；只有长期有效的结论才沉淀到这里。

**空泛话术禁止**：不写"部分模块缺少异常处理"，写"[文件路径:行号] 的 [方法名] 捕获 `Exception` 后静默忽略，无日志无重试，[具体影响]"。

---

## modules/&lt;module&gt;.md（核心，模板见 `assets/docs/specs/modules/_module-template.md`）

一个模块文件覆盖该模块的定位、对外契约、内部实现事实、边界规则，人类打开一个文件读完全貌，AI 对照一个文件完成契约核查。

### 取证顺序（每个模块执行一次，命令中 `[模块名]` 替换为实际模块名/关键词）

```bash
# 1. 实体/模型类（数据模型字段来源）
# Python
find . -path "*/models/*" -name "*.py" 2>/dev/null | head; grep -rl "class .*Base\|SQLModel\|Base)" --include=*.py . 2>/dev/null | head
# Node
find src -path "*model*" -o -path "*entity*" 2>/dev/null | head; grep -rl "@Entity\|@Schema\|z\.object" --include=*.ts src 2>/dev/null | head
# JVM
find . -path "*/po/*" -name "*[模块名]*.java" 2>/dev/null; find . -path "*/entity/*" -name "*[模块名]*.java" 2>/dev/null

# 2. 状态枚举（状态机来源）
grep -rEl "class .*Status|enum .*Status|StatusEnum|StateEnum" --include=*.py --include=*.ts --include=*.java . 2>/dev/null | head

# 3. 应用服务（调用树来源）
find . -name "*[模块名]*Service*" -o -name "*[模块名]*Application*" 2>/dev/null | head

# 4. 入口（接口契约来源）
# Python (FastAPI/Flask/Django)
grep -rE "@app\.(get|post|put|delete|patch)|@(app|router)\.(get|post)|@api_view|path\(" --include=*.py . 2>/dev/null | head -40
# Node (Express/Koa/Nest/Fastify)
grep -rE "\.(get|post|put|delete|patch)\(|@Controller|@Get|@Post" --include=*.ts --include=*.js src 2>/dev/null | head -40
# JVM
find src/main -name "*Controller*.java" | xargs grep -l "@RequestMapping\|@RestController" 2>/dev/null
find src/main -name "*.java" | xargs grep -l "@CraneTask\|@XxlJob\|@Scheduled" 2>/dev/null
# Go (net/http / gin / echo)
grep -rE "http\.Handle|\.GET\(|\.POST\(|r\.Handle|mux\.Handle" --include=*.go . 2>/dev/null | head -40
# 通用契约文档
find . -name "swagger*.yaml" -o -name "openapi*.yaml" -o -name "openapi*.json" 2>/dev/null | head -3

# 5. 数据访问层（索引/查询用途推断）
find . -name "*[模块名]*Mapper*" -o -name "*[模块名]*Repository*" -o -name "*[模块名]*Repo*" 2>/dev/null | head
```

有 `contract.md`（§F6 触发）时：接口/数据/行为/边界**必须**先从 `contract.md` 提取（已是确认过的事实），代码取证仅作补充与交叉验证。有参考文档且无 `contract.md`（R0' 独立触发）时：业务定位/流程步骤等语义信息结合参考文档，代码取证验证。

### § 定位

一句话说明模块职责 + 核心业务场景，不写"负责xxx相关的管理功能"这类空话，要写"[模块名] 管理 [业务实体] 的全生命周期，核心表 [table_name]，聚合根实体 [EntityClass]"。

### § 接口契约

按类型分节（HTTP / RPC / MQ / Cron）：

1. HTTP 接口：按 Controller/Router 分节，每节一句话描述（优先从注解/装饰器/JSDoc 取，无则从方法名推断），列路径表：路径 / 方法 / 入参 / 返回 / 错误情况。
2. MQ 消费：topic/group + 处理逻辑（读 Consumer 实现）。
3. Cron：任务名 / 所在类 / 入参格式。

**废弃/疑似未上线接口处理**（发现即走此流程，不静默标注）：

```
1. 检查废弃注解（@Deprecated / @deprecated / deprecated）→ 有则标注废弃时间（无则留空）
2. 检查是否有替代接口说明 → 有则备注"已被 /xxx/new 替代"
3. 用 AskUserQuestion 逐个询问：
   "发现存疑接口：[接口路径]，请决策：1. 保留并加废弃标注  2. 从文档删除"
4. 根据用户决策更新文档
```

**质量基线**：每个接口都有一句话描述（非模板占位）；路径完整（非 `/[TODO]`）；错误情况列全。

**空泛话术禁止**：不写"处理相关请求"，写"创建或更新 [业务实体]，含 [字段1]/[字段2] 等参数（`xxx_controller.py:42`）"。

### § 数据模型（无内部 DB 工具，纯代码取证）

**取证流程（每张表/集合执行一次）**：

1. 读实体/模型类，记录每个字段的**代码字段名**和**代码类型**。
2. **DB 列名/类型/约束/默认值**从以下代码来源取证（按可用性优先）：
   - ORM 声明：SQLAlchemy `Column(...)`、Django `models.XxxField(...)`、JPA `@Column`、Prisma schema、GORM struct tag、Sequelize define。
   - migration/DDL 文件：`migrations/`、`*.sql`、`db/migrate/`、Alembic/Flyway/Liquibase 脚本。
   - 提取：DB 列名、DB 类型、NOT NULL、DEFAULT、COMMENT/注释。
3. 按列名对应，合并为 5 列表格：

   | DB 列（DB 类型） | 实体字段（代码类型） | 非空 | 默认值 | 说明 |
   |---|---|---|---|---|

   - 非空：NOT NULL → `✓`；允许 NULL → `—`。
   - 默认值：有 DEFAULT → 填值；无 DEFAULT 且允许 NULL → `NULL`；无 DEFAULT 且 NOT NULL → `—`。
   - 说明：优先取 DDL/字段注释；无则从字段名语义推断。
   - 跨模块引用字段在说明末尾标注 `→ 外部模块.实体`。
   - 跳过审计字段：`creator / creator_id / creator_name / modifier / create_time / update_time`；保留 `id` 和软删字段（软删字段在表头统一说明，正文行跳过）。
4. **对应实体**：必须填写实体类名，不可省略。
5. **索引填充**：从 ORM/migration/DDL 提取所有索引/KEY 定义；对每个非主键索引，读数据访问层 SQL/查询方法，推断查询用途；能推断写 `索引名 (字段列表)` — 用途一句话，推断不出只写 `索引名 (字段列表)`，不加说明，不写 `[TODO]`。

**降级处理**：某表完全无 ORM/migration/DDL 证据（如纯 NoSQL 无 schema、外部库表）→ 该表数据模型节标 `[TODO: 无代码侧 schema 证据，需人工核查]`；单表失败只降级该表，继续完成其他章节。

**质量基线**：每张表/集合的`对应实体`必须填写；5 列表格完整，非全 `[TODO]`。

### § 行为（状态机 / 调用树）

**状态机**（模块存在状态流转时填）：

1. 读所有 Status/State 枚举类，列出全部状态值：`枚举名(数字)  描述`。
2. 对每个非初始状态，找执行状态变更的方法（搜 `update_status` / `status =` / `setStatus` 等赋值）。
3. 追踪到入口，确认触发该调用的 API 路径。
4. 构建文本流程图：状态节点 `STATE_NAME(value)  中文描述`；转换箭头 `│  POST /path` + 主要前置条件（一句话）。
5. 双向流转（退回操作）分别标注两个方向触发接口。

**调用树**（模块存在复杂业务流程时填，对每个主要业务操作执行一次）：

1. 从入口确认 API 路径和应用服务入口方法名。
2. 读该方法的**完整代码**，按调用顺序构建树：
   - **方法名必须与代码完全一致**，逐字复制，不改写不简化。
   - 忽略工具性调用：`log.xxx`、getter/setter、`requireNonNull`、简单 null 检查等。
   - 前置校验方法（`check_xxx` / `validate_xxx`）统一归入 `[前置校验]` 子树。
   - 每个方法调用后加中文注释：先看其 JavaDoc/docstring；无则从方法名语义推断。
   - 重要业务分支用 `[条件]` 标注，列出两个分支。
   - 最深 **3 层**，不超过。
3. 调用树前写 2-4 句描述：谁发起、做什么、有哪些前置限制、触发哪些副作用。

**质量基线**：状态机每条转换线都有触发接口路径；调用树所有方法名与代码完全一致。

### § 边界规则

从 `contract.md` 的边界契约、或代码中的权限/审批/依赖方向守卫提取，逐条列出：

```bash
grep -rE "require_role|requires_permission|@PreAuthorize|roleId|operatorId|current_user" --include=*.py --include=*.ts --include=*.java . 2>/dev/null | head -10
grep -rE "BusinessException|ErrorCode|raise .*Error|ValidationError" --include=*.py --include=*.ts --include=*.java . 2>/dev/null | head -20
```

**候选规则条件**：涉及本模块或跨模块调用 + 有明确错误码或代码守卫 + 有稳定触发时机。

**空泛话术禁止**：不写"系统对业务操作有严格约束"，写"[业务实体] 处于 [终态] 后禁止 [具体操作]，违反返回 [错误码]（来源：`xxx_service.py:[行号]`）"。

### § 名词（本模块内术语，跨模块术语放 glossary.md）

纳入标准：代码注释/字段说明/枚举描述中频繁出现，但**不看代码或不了解业务背景则难以理解**的词。排除纯技术术语和系统通用概念。数量通常 3-10 个；确实无需解释可写"本模块无需额外解释的业务术语"。

### § 上下游

- **上游依赖**（本模块消费外部数据）：在应用服务中找 `query_xxx` / `get_xxx` / `check_xxx` 等读取其他模块数据的调用。表格：`依赖方 | 触发接口 | 依赖内容`。
- **下游影响**（本模块触发其他模块或外部系统）：找 `batch_update_xxx` / SDK 调用 / MQ 发送等写操作。表格：`被影响方 | 触发接口 | 触发内容`。

### 模块文件整体质量基线

- 定位具体（提到业务域和核心场景），非"负责xxx相关功能"这类空话。
- 每张表/集合的`对应实体`必须填写。
- 调用树所有方法名与代码完全一致；状态机每条转换线都有触发接口路径。
- 名词有实质内容（≥3 条，或明确说明无需解释理由）。
- 数据模型 5 列表格完整，非全 `[TODO]`。

---

## 首次初始化 vs 增量更新

**首次初始化**（`docs/specs/` 不存在或目标模块文件不存在）：从 `assets/` 读模板起新建，走完整取证流程。

**增量更新**（模块文件已存在）：先读现有文件作基线，只更新本轮 `contract.md`/代码改动命中的章节；未命中的章节原样保留，不重新生成。判断"是否命中"：contract.md 声明的接口/数据/行为/边界 变化点 → 精确定位到模块文件对应小节。

## worker subagent 派发（§F6 命中后的同步执行）

同步动作边界清晰（改哪些模块文件、依据哪份 contract.md）时，主 agent 可派 worker subagent 执行，自己不占用主线程做取证细节。

**派发至少给出**：
- 目标仓库路径。
- 依据来源：任务目录 `contract.md` 路径（§F6 触发）或指定代码范围（R0' 独立触发）。
- 受影响模块文件路径清单（已存在则连同现有内容一起给，作增量基线；新建则给模块名与 `assets/docs/specs/modules/_module-template.md`）。
- 写权限范围：只能写列出的模块文件 + 若涉及则 `glossary.md`/`risks.md`/`index.md`，不得新建列表外文件。
- 完成后须回报：本轮更新了哪些章节、新增了哪些术语/风险条目（供主 agent 判断是否合理）、遇到的取证缺口（`[TODO: ...]` 清单）。

**worker 不做**：判断 PASS/FAIL、决定是否需要同步（这是主 agent 在 §F6 的职责）、跨模块的术语取舍决策（拿不准交回主 agent 或询问用户）。

主 agent 收到 worker 结果 → 检查写入范围是否越界 → 汇总本轮变更 → 新开 strict-validation subagent 审稿。**禁止 worker 自己判定通过、自己派发 reviewer。**
