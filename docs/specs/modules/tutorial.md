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

# tutorial 模块

> 这是什么：新手动手教程（`website/tutorial/`，8+1 课）的事实资产登记。更新日期：2026-09-09。对应代码模块：无独立代码——教程引用全仓库各模块的命令/路径/事件名，本文件是"教程引用了哪些外部事实"的登记簿，供代码演进时的漂移抽查联动（改了下表左侧事实 → 必须检查教程对应课是否要跟着改）。

## 定位

tutorial 模块管理教程对仓库外部事实的引用清单：教程正文承诺的每条命令、路径、事件名、命名空间约定都登记在此。它不是教程的目录或大纲（那在 `website/tutorial/index.md`），而是"教程 vs 代码"的一致性锚点。

## 接口契约

### HTTP

无（纯文档模块）。

### RPC / MQ / Cron

无。

## 数据模型

无表。本节登记教程引用的**命令与 CLI 语义**（左侧改动 → 右侧联动检查）：

| 事实（代码侧） | 教程引用位置 | 改动时须检查 |
|---|---|---|
| `luban migrate` = alembic upgrade head | 01 课步骤 3 | 01 课概念段 |
| `luban init` 幂等：内容未变输出 `(unchanged)`、有变化才追加版本；只填空位播种槽位 | 01 课步骤 3 检查点、02/03 课主线 | 01/02/03 课 |
| `luban init` 扫描 `app/agents/*.yaml` 导入，必填仅 name+instruction | 02 课主线 | 02 课 |
| `luban export <key> [--output] [--version]` | 02 课主线 | 02 课 |
| `luban eval run <agent[@version]> --dataset X [--compare N] [--save Y] [--auto-approve]`，回归命中 exit 1；启动时独立进程装配三件套（扩展扫描/知识源自举/审批 manager 按需） | 06 课主线 | 06 课 |
| `scripts/dev.sh start\|stop\|status\|restart\|logs`，端口 API 8000 / UI 5173，**不启动 mock-biz**（mock 启动见下一行） | 01 课步骤 4/10 | 01/03 课 |
| `python -m demos.customer_service.mock_services` 监听 9100；订单 ORD-1001 返回"无线蓝牙耳机/299"，ORD-1006 返回机械键盘/899 可退款 | 03 课步骤 5/6、常见坑 | 03 课、demos README |
| FakeLLM 行为：固定单句剧本台词（不做工具调用、不做检索——零密钥下仅验证链路） | 01 课常见坑 | 01 课 |
| 真模型 + 种子人设行为：查单/退款需输入带全信息（退款必须带原因才触发 refund_apply）；FAQ 回答带 `[n]` 引用 | 03 课常见坑/步骤 5、05 课步骤 6 | 03/05 课 |
| `cp app/config/.env.example .env` 零配置可跑（SQLite + FakeLLM） | 01 课步骤 2 | 01 课 |
| `PUT /v1/models` + `PUT /v1/brain-mappings {profile, model_key}` 两步接真模型 | 03 课主线 | 03 课、README 快速开始 |
| `POST /v1/agents/{key}/runs` 请求体 `{input, session_id, user_id, version, variables, history, instructions_override}`，返回 SSE | 01 课步骤 8 | 01/03 课 |
| SSE 事件名（教学子集）：run.started / token.delta / tool.started / tool.finished / tool.failed / tool.approval.requested / tool.approval.resolved / knowledge.no_hit / run.completed | 00 课 §8、01 课步骤 8 检查点、03/04 课审批正文 | 00/01/03/04 课（全量 15 种以 `lubanagent/runtime/sse.py` SSE_EVENT_MAP 为准） |
| 审批闭环：/chat 页气泡内「批准/拒绝」按钮消费 decide 端点（`POST /v1/approvals/{id}/decide {approved, decided_by}` → `{"status": "approved"}`），Run 恢复 | 03 课步骤 6、04 课步骤 6 | 03/04 课 |
| 工作台页面路由：/agents、/agents/:key/debug、/prompts、/models、/tools、/skills、/knowledge、/graphs、/traces、/evals；用户端 /chat（含审批按钮） | 01 课步骤 6/7/9 | 01 课 |
| 知识 API：`POST /v1/knowledge/sources`（multipart，8 格式）、`POST /v1/knowledge/query {source, query, top_k}` 检索测试 | 05 课主线 | 05 课 |
| 图编排 DSL：GraphBuilder 的 entry/step/agent/human/when/edge/build；`@graph` 注册；flow_as_tool | 07 课主线 | 07 课 |
| TeamBuilder：supervisor/agent/route(max_delegations, entry_member, fanout)；teams run 走用户领地自定义路由 `/v1/teams/<key>/runs` | 07 课主线 | 07 课 |
| docker-compose 五服务：app(:8000)/mysql/langfuse(:3000)/langfuse-db/mock-biz(:9100)，容器内自动 migrate+init | 08 课主线 | 08 课、README |
| `.lubanagent-upstream` 领地清单 + CHANGELOG 领地标签 + UPGRADE.md 升级合并 | 08 课升级演练 | 08 课 |

## 行为

### 状态机

无。

### 调用树

无。

## 边界规则

- **学生产出物命名空间**：教程约定文件型产出物（YAML / Python / 数据集 / 图 / 团队，如 `app/agents/my_lib_assistant.yaml`、`app/tools/my_library/`、`app/datasets/my_lib_eval.yaml`、`app/graphs/my_library_flow.py`、`app/multiagents/my_library_team.py`）统一 `my_` / `my-` 前缀；知识源名等库内对象不强制前缀（如 05/07 课的 `library-rules`——库内对象不是文件产出物，只需不与种子撞名）。**仓库种子不得使用 `my` 前缀 key**——新增种子 Agent/Tool/数据集时若占用该前缀，须与教程协商。
- **教程不复制手册全表**：字段全量、API 全参数、全部事件只在用户手册维护；教程只出现当课子集，文末链接手册章节。手册章节号调整 → 教程"延伸阅读"联动。
- **检查点真实性**：教程"你应该看到"的内容来自写作时的真实执行输出；涉及输出会漂移的（版本号、pid），课文已注明"跟你的不一样是正常的"。
- **reference/ 是唯一参考答案源**：`website/tutorial/reference/` 维护图书馆主线版参考产出物，不维护学生自选场景的变体。

## 名词

- **主线项目**：贯穿教程的图书馆借阅助手 `my-lib-assistant`，与内置客服示例逐能力同构（查借阅↔查单、挂失↔退款、规章 FAQ↔产品 FAQ、逾期流程↔退款流水线、咨询台团队↔客服分流团队）。
- **投喂口/存储**：教程对"YAML 仅用于 init 导入/导出迁移、数据库才是 Agent 定义的存储"的口语表述（对应用户手册 §1）。

## 上下游

- **上游（被引用方）**：全部功能模块（runtime / model / tool-mcp / knowledge-memory / graph / multiagent / api-app-cli / observability-*）——本文件上表即依赖清单。
- **下游**：`README.md` / `docs/README.en.md`（教程入口链接）、`website/manual/`（使用手册反向链接“新手先做教程第 N 课”）、`demos/customer_service/README.md`（文首教程指引一行）。
- 漂移抽查联动：本文件属 `docs/specs/` 资产目录，纳入既有全仓漂移抽查；累计计数与抽查规则见 `.spec-coding/progress.md`。
