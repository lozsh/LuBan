# 升级指南

> LubanAgent 是"若依式起步基座"——你克隆仓库后长期住进去迭代、上生产。本文告诉你怎么跟上游版本升级，而不丢掉自己的改动。

升级的底层保证是**领地三重隔离**（架构设计 §4）：`lubanagent/`（框架领地，上游追踪）、`app/`（你的领地，上游永不主动触碰）、`demos/` `tests/` `docs/` `migrations/` 等（共享领地）。依赖方向单向 `app → lubanagent` 允许、反向禁止，由 `importlinter.toml` 在 CI 强制；上游 PR 是否越界改了你的领地，由 `.lubanagent-upstream` 清单 + `scripts/check_upstream_guard.py` 守卫。

---

## 1. 升级总则

**是什么**：你 fork/clone 仓库后改造，上游持续演进，你定期把上游变更拉回自己的代码库。

**为什么**：框架领地（`lubanagent/`）的 bug 修复、新能力、性能优化只有跟上游才能拿到；而你的业务改动在 `app/` 里，架构上保证不冲突。

**怎么用**：一次性配置 upstream remote，之后每次升级走"拉取 → 合并 → 迁移 → 验证"四步。

```bash
# 一次性：登记上游仓库（替换为真实上游地址）
git remote add upstream https://github.com/<org>/Luban.git

# 每次升级
git fetch upstream                              # 拉取上游最新
git checkout master                             # 在你的主分支上操作
git merge upstream/master                       # 合并上游（推荐，保留合并节点）
# 或：git rebase upstream/master               # rebase 把你的提交重放到上游之上（历史线性，但会改写你的提交哈希）
```

合并策略选择：
- **`merge`（默认推荐）**：保留合并节点，你的提交哈希不变，协作友好。冲突就地解决。
- **`rebase`**：历史线性、整洁，但会重写你的提交哈希；若你的分支有协作者或已 push，慎用。

冲突主要发生在哪：
- **`lubanagent/`（框架领地）**：你若改过框架代码，会与上游冲突——优先采纳上游，把你的定制改为扩展点实现放进 `app/tools/` 或 `app/graphs/`（由 `EXTENSION_PACKAGES` 扫描）；或在 `app/main.py` 手动注册。
- **`app/` 内种子文件**（`app/main.py`、`app/agents/customer_service.yaml` 等清单内文件）：上游做破坏性变更时才动，会与你的改动冲突——见第 2 节 `[scaffold-seed]`。
- **`app/` 内你的文件**：上游永不主动触碰。`.lubanagent-upstream` 守卫在 CI 拦截上游对 user-owned 路径的改动，显著降低合并冲突——但守卫不承诺任意 git merge 零冲突（未登记的新路径、极端重名等仍可能冲突，合并后自查）。
- **共享领地**（`docs/` `tests/` `docker/` 等）：按需手工合并。

**边界**：本文假设你以 `master` 跟上游。若用长期分支，把 `upstream/master` 换成对应上游分支。`git pull --rebase upstream master` 是 `fetch + rebase` 的简写，等价但不便于冲突时分步排查。

---

## 2. 领地与合并策略

CHANGELOG 每条变更按领地打标签（`[framework]` / `[scaffold-seed]` / `[shared]` / `[migration]` / `[breaking]`）。升级时按标签找对应合并动作。

### `[framework]` —— 框架领地 `lubanagent/`

**是什么**：框架本体变更。上游追踪，你原则上不动。

**为什么**：这是你跟上游升级的核心收益——bug 修复、新能力、治理增强都在这里。

**怎么用**：直接合并，通常无冲突（前提是你没改过框架代码；改过则见上文冲突处理）。

```bash
git merge upstream/master
# 若你在 lubanagent/ 下改过且冲突：
git checkout --theirs -- lubanagent/        # 采纳上游
git checkout --ours  -- lubanagent/        # 保留你的改动（不推荐，会脱离上游）
# 最佳实践：把定制移出框架领地，放进 app/tools/ 或 app/graphs/（由 EXTENSION_PACKAGES 扫描）；或在 app/main.py 手动注册
```

**边界**：若你发现必须改 `lubanagent/` 才能满足需求，多半是缺一个扩展点——向上游提 issue 而非就地改。扩展点 Protocol 变更见 `[breaking]`。

### `[scaffold-seed]` —— 脚手架种子 `app/` 内初始文件

**是什么**：首次克隆提供"能跑起来"的种子文件（`app/main.py`、`app/config/.env.example`、客服示例的 4 个 Agent YAML、`refund_flow.py`、`cs_team.py` 等，完整清单见 `.lubanagent-upstream` 的 `[scaffold-seed]` 节）。上游仅在做破坏性变更时才动这些文件。

**为什么**：种子是你的起点，不是你的业务代码。上游改种子是为了让新克隆者拿到更新的示例，而你已有自己的演化版本。

**怎么用**：合并时保留你的改动，只取上游的破坏性修正。

```bash
git merge upstream/master
# 冲突时保留你的版本（种子文件你已演化）：
git checkout --ours -- app/main.py app/agents/customer_service.yaml
git add app/
git merge --continue
# 若想参考上游新版种子手动 cherry-pick 改动点：
git show upstream/master:app/main.py > /tmp/upstream_main.py
diff app/main.py /tmp/upstream_main.py
```

**边界**：种子文件与 `user-owned` glob 重叠时，以 `[scaffold-seed]` 为准（上游可动）。你在 `app/` 下新增的文件不在种子清单内，属于 `user-owned`，上游永不触碰。

### `[shared]` —— 共享领地 `docs/` `tests/` `docker/` `scripts/` `migrations/` `.github/` `pyproject.toml` 等

**是什么**：上下游都会演进的共享资源。完整清单见 `.lubanagent-upstream` 的 `[shared]` 节。

**为什么**：测试基建、CI 配置、Docker 编排、文档需要随框架一起更新；但你可能有自己的定制（如 CI 秘钥、Docker 镜像标签）。

**怎么用**：手工合并，逐文件判断。

```bash
git merge upstream/master
# docs/ 、tests/ 通常直接采纳上游（你很少改）
# pyproject.toml：若你加了依赖，合并时保留你的 [project.optional-dependencies] 段
# docker-compose.yml：若你改了服务名/端口，合并时手工对齐
```

**边界**：`pyproject.toml`、`alembic.ini`、`importlinter.toml` 这类配置文件若你改了依赖方向或迁移配置，合并冲突需谨慎——错的合并可能破坏领地隔离契约，升级后务必跑 `lint-imports`（见第 6 节）。

### `[migration]` —— 数据库 schema 变更

**是什么**：Alembic 迁移链新增 revision（`migrations/versions/` 下新文件）。

**为什么**：表结构演进必须随代码升级一起执行，否则 ORM 模型与库表不一致。

**怎么用**：合并代码后立即执行迁移（见第 4 节）。

```bash
luban migrate                # 等价 alembic upgrade head
```

**边界**：迁移是累加的，不能跳版本。若你落后多个版本，逐个 revision 升级，不要直接跳到 head 而跳过中间的数据迁移逻辑。

### `[breaking]` —— 扩展点 Protocol 或 core 契约不兼容变更

**是什么**：`ChannelConnector` / `SessionStateStore` / `OutputGuard` 等扩展点 Protocol 签名变更，或 `core` 契约不兼容。会升 major 版本号。

**为什么**：这类变更无法自动合并，你的扩展实现需要手工适配。

**怎么用**：每次 `[breaking]` 发布会在 CHANGELOG 标注，并在本文件第 5 节给出迁移示例。升级步骤：

```bash
git merge upstream/master
# 按 UPGRADE.md 第 5 节的迁移示例，修改 app/tools/ 或 app/graphs/ 下受影响的扩展实现
# 跑测试确认：
python -m pytest
```

**边界**：`[breaking]` 不保证向后兼容，升级前务必读 CHANGELOG 对应条目与本文件第 5 节。生产环境升级前先在 staging 验证。

---

## 3. .lubanagent-upstream 守卫

**是什么**：仓库根的 `.lubanagent-upstream` 清单，用 glob 列出四类路径：`[framework]`（上游追踪）、`[shared]`（共享）、`[scaffold-seed]`（种子）、`[user-owned]`（上游永不主动触碰）。CI 脚本 `scripts/check_upstream_guard.py` 比对上游 PR diff 是否命中 `user-owned` glob，命中即红。

**为什么**：这是“用户领地低冲突”的机制保障。上游维护者发 PR 时，CI 自动拦截对清单内 user-owned 路径的改动——你的业务代码不会因为上游合并而被动改写。守卫降低冲突概率，不承诺任意 git merge 零冲突。

**怎么用**：

作为升级者，你无需运行守卫脚本（那是上游 CI 的职责）。但你应该在自己的 fork 里维护这份清单：

```bash
# 你在 app/ 下新增业务目录时，确认它落在 user-owned 保护下
# .lubanagent-upstream 的 [user-owned] 节已用通配规则覆盖：
#   app/agents/!(customer_service|cs_faq|cs_refund|cs_triage).yaml
#   app/tools/!(customer_service)/
#   app/extensions/
#   app/config/!(.env.example)   # config 下除种子样本外全部 user-owned
#   ... 等
# 你新增的 app/agents/my_bot.yaml 自动被 !(customer_service|...) 规则保护，无需改清单。

# 仅当你在 app/ 外（如根目录）新增用户文件时，才需在 [user-owned] 补 glob。
```

守卫脚本本地可手动跑（用于自查上游 diff 是否越界）：

```bash
# CI 同语义：只审 upstream/master...HEAD 的提交 diff
python scripts/check_upstream_guard.py --base upstream/master

# 本地自查：额外纳入未提交 tracked 修改与未跟踪新用户路径
python scripts/check_upstream_guard.py --base upstream/master --include-worktree
```

**边界**：守卫只防"上游主动改用户领地"，不防"用户改框架领地"——后者靠 import-linter 的依赖方向契约约束。`user-owned` 与 `scaffold-seed` 重叠时以 `scaffold-seed` 为准（种子文件上游可动）。

---

## 4. 数据库迁移

**是什么**：每次含 `[migration]` 标签的发布，需要执行 Alembic 迁移把库表升级到最新 schema。

**为什么**：LubanAgent 的 Agent 定义、模型映射、Run/Trace、评测、审批记录等是库内记录（架构 §4："定义本体是数据库记录，运行期可改、库内版本化"），schema 与代码强绑定。审批记录自迁移 `0010` 起落 `approvals` 表（决定不随进程重启丢失、可审计）。

**怎么用**：

```bash
# 升级到最新
luban migrate                          # 推荐，等价 alembic upgrade head
# 或直接用 alembic：
alembic upgrade head

# 回滚一个 revision
alembic downgrade -1

# 回滚到初始（清空所有表结构，慎用——数据会丢）
alembic downgrade base

# 查看当前版本
alembic current
```

开发期与生产期的差异：

| 场景 | 数据库 | 配置方式 |
|------|--------|----------|
| 开发期 | SQLite（免安装，零配置） | `.env` 不设 `DATABASE_URL` 即默认 SQLite，`luban migrate` 自动建库 |
| 生产期 | MySQL 8.0+ | `.env` 设 `DATABASE_URL=mysql+asyncmy://user:pass@host:3306/lubanagent`，先建库再 `luban migrate` |

MySQL 驱动需装可选依赖：`uv pip install -e ".[mysql]"`。

**边界**：迁移链是线性的（当前 `0001 → … → 0010`），不能跳版本。回滚 `downgrade base` 会删表，生产环境务必先备份。三向迁移冒烟（upgrade → downgrade → upgrade）已在 CI 固化，但那只验证 schema 可逆，不验证你的业务数据可逆。

---

## 5. 当前版本升级注意事项

**是什么**：从上一版升级到当前版需要手动做的动作清单。本节随每次发布更新。

**为什么**：自动合并能处理代码，但 `[breaking]` 变更、配置项新增、依赖变化需要你手工介入。

**当前版本 `[Unreleased]`（2026-08）**：从 `[0.1.0]` 升级需要做三件事：

```bash
git merge upstream/master
# ① 依赖：知识库管理新增 pdf/docx 解析（已含在 dev extra；生产/最小安装补 docs extra）
uv pip install --python .venv/bin/python -e ".[docs]"
# ② 迁移：新建 knowledge_sources 表（工作台托管知识源）+ approvals 表（审批记录落库，R11）
.venv/bin/luban migrate
# ③ scaffold-seed：app/main.py 种子源装配改用工厂共享实例（见 CHANGELOG 合并指令，
#    已原地改过 app/main.py 的用户保留自己的版本，只把 store/embedder 来源改为
#    app.state.knowledge_store / app.state.knowledge_embedder）
.venv/bin/python -m pytest           # 验证
```

配置项新增 `KNOWLEDGE_FILES_ROOT`（知识库上传文件落盘根目录，默认 `data/knowledge`），容器部署建议挂持久卷（见 docker-compose.yml `knowledge-data`）。

**MCP 服务升级**：`create_mcp_server()` 不再默认导出 Registry 中的所有工具，升级后须显式传入 `export_tools`（仅列出要开放的工具 key）；未指定时导出为空。MCP 工具调用经 `ToolExecutor` 的权限、风险与审批检查，高风险工具若没有审批器将被拒绝；需由部署方按实际场景配置审批器，不要为兼容旧行为自动批准。HTTP MCP 入口须单独配置 `http_api_key`，未配置时即使直接挂载 ASGI 也拒绝请求；它不复用 REST API Key。stdio 仅用于受信本地调用。

**限流与审批客户端升级**：限流桶现在按已验证的 `Principal.subject` 计数；关闭鉴权时所有请求共用 `anonymous` 桶，轮换 Bearer 或发送 `X-Forwarded-For` 不再分配新额度。若原先依赖匿名多桶，需启用鉴权并通过可信 `AuthProvider` 分配主体；进程内限流默认至多 4096 个主体桶，容量满时新主体收到 429，多实例计数仍不共享（`lubanagent/api/auth.py:46-94`）。审批决定接口保留请求字段 `decided_by` 以兼容旧客户端，但忽略其值，归因使用服务端验证的主体；`DecideOut`/`ApprovalOut` 增加 `decided_by`、`decided_at`，消费端可按需读取，不能继续依赖客户端填值伪造归因（`lubanagent/api/routes/approvals.py:18-56`、`lubanagent/api/schemas.py:67-88`）。**审批记录已持久化**：`create_app` 默认装配 `ApprovalManager(store=DbApprovalStore(db))`，决定落 `approvals` 表（迁移 `0010`）、不随进程重启丢失、不重复（decide 条件更新原子裁决）、可审计回查；`ApprovalManager()` 缺省仍为进程内 store（库内嵌/测试零迁移，需持久化时显式注入 `DbApprovalStore`）。**边界**：审批记录持久化 ≠ Run 自动恢复——普通 Run 已有逐步 checkpoint（见下方「Run checkpoint 与 resume」），但恢复须显式调 resume API（非终态且有 checkpoint），无自动重启恢复，审批闸门语义不随 resume 改变；跨进程 decide 感知延迟上限 ≈ 轮询间隔（默认 2s）。共享 API Key 只能标识 Key 持有者，不能证明具体人员或职责分离（`lubanagent/tool/approval.py:87-195`、`lubanagent/api/auth.py:21-30`）。

**Run checkpoint 与 resume（2026-10）**：`create_app` 缺省装配 `LubanCheckpointer(db)`——普通 Agent Run 从此在每个节点边界落 `checkpoints` 表（表由 0001 迁移建立，**零新迁移**）；崩溃/中断后的非终态 Run 可显式调 `POST /v1/runs/{run_id}/resume` 续跑（SSE，帧格式同创建 Run；校验在流开始前报错：Run/checkpoint 缺失 404 `RUN_NOT_FOUND`，终态/活跃/子 Run 422 `VALIDATION_ERROR`，未启用 checkpoint 500 `CONFIG_ERROR`）。**行为变化**：默认启用后普通 Run 会新增 checkpoint 写入；不想要这部分 DB 写入则 `create_app(checkpointer=None)` 显式关闭（此时 resume 返回 `CONFIG_ERROR`）。`luban eval run` 独立进程同语义装配（仅本进程可续跑）。**边界**：恢复是显式 API 调用、无自动重启恢复；互斥保护仅限单进程（多 worker/多实例部署勿对同一 Run 双 resume，风险 R14）；子 Run 归父编排、不可独立 resume；审批闸门语义不随 resume 改变（`lubanagent/runtime/agent_loop.py::stream_resume`、`lubanagent/persistence/checkpoint.py`）。

**审批 Python API 破坏性变更（库内嵌消费方必读）**：`ApprovalManager` 的 `create_request`/`decide`/`get`/`pending` 由同步改为 **async**，`decide` 现在返回终结态 `ApprovalRequest`（原先返回 None）。按旧同步写法调用的代码不会报错但会**静默失效**（拿到 coroutine 未 await），升级时须补 `await`，例如 `request = await manager.create_request(...)`、`resolved = await manager.decide(id, True)`。`ApprovalRequest` 领域对象已从 `lubanagent.tool` 迁至 `lubanagent.core`（`from lubanagent.core import ApprovalRequest`）；`lubanagent.tool` 保留 re-export，旧 import 路径仍可用。

**模型参数与连接边界收紧（H06）**：模型定义的 `params` 默认收紧为受控白名单——`openai_compat` 工厂仅允许推理与计价字段（`temperature`/`max_tokens`/`top_p`/`frequency_penalty`/`presence_penalty`/`n`/`stop`/`seed`/`timeout`/`max_retries`/`streaming`/`input_price_per_1k`/`output_price_per_1k`，`lubanagent/core/model.py:59-75`）；`base_url` 拒绝携带凭证（userinfo）、查询参数或片段的连接地址，`params` 拒绝覆盖连接字段（`model`/`api_key`/`base_url`/`default_headers`/`http_client`/`proxy` 等）与秘密形键（`*apikey`/`token`/`password`/`secret`/`credential` 等）。此前任意 `params` 会透传给 `ChatOpenAI` 构造并全量回显；**存量含自定义 `params` 的模型定义升级后执行会被拒绝**（`create_openai_compatible` 构造入口统一校验，`lubanagent/model/openai_compat.py:27-35`），需把自定义参数迁移为白名单字段或移除；工作台模型列表对白名单外字段只显示 `[REDACTED]`（`lubanagent/api/routes/models.py:14-25`）。本地 vLLM/Ollama 等 OpenAI 兼容内网地址不受影响（`base_url` 只禁凭证/查询/片段，不一刀切禁内网）。

**种子与演示部署边界收紧（H08）**：①种子模型定义从 upsert 改为"只填不存在"——`luban init` / 装配期 `seed_defaults` 不再覆盖已存在的同 key 模型定义（此前每次 init 会把工作台改过的 `fake-chat` 参数打回种子值）；显式 `luban init` 的 Agent YAML 幂等导入语义不变。②`luban init` 新增 `--fill-missing`：只导入不存在的 Agent，已存在一律跳过；`scripts/dev.sh` 自动启动已改用该模式，磁盘旧 YAML 不会把你经工作台做的 Agent 修改追加成回退版本。若自建启动脚本依赖旧的"init 覆盖"行为，请改用显式导入。③演示 compose 全部宿主端口改绑 `127.0.0.1`（app 8000 / langfuse 3000），mock-biz 不再映射宿主端口（app 走容器网络）；从宿主机/外部机器直连这些端口需自行调整映射。头像文件新增 `AVATAR_FILES_ROOT=/data/avatars` + `avatars-data` 卷持久化；`docker compose up` 重建容器后知识库与头像不再丢失。`.dockerignore` 递归排除任意层级 `.env*`（样本 `app/config/.env.example` 除外）与私钥形态文件。

**上一版 `[0.1.0]`（2026-07-27）**：首个发布版本，无上一版，无需升级动作。新克隆者按以下步骤初始化即可：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp app/config/.env.example .env       # 零配置：SQLite + FakeLLM 兜底
.venv/bin/luban migrate               # 建表
.venv/bin/luban init                  # 初始化（导入种子 Agent/Tool 等）
```

后续版本若有 `[breaking]` 变更，将在此给出具体迁移示例（修改哪个扩展点、改什么签名、加什么配置项）。

**边界**：本节只列"从上一版到当前版"的增量动作，不重复 README 的快速开始。完整安装步骤见 `README.md`。

---

## 6. 升级 Checklist

**是什么**：每次合并上游后该跑的验证清单。

**为什么**：合并可能引入配置漂移、依赖方向破坏、迁移未执行等问题，逐项验证才能确认升级干净。

**怎么用**：合并完成后依次执行，全绿即升级成功。

```bash
# 1. 代码风格 lint（与 CI backend job 第一步一致）
.venv/bin/ruff check lubanagent tests migrations app demos scripts

# 2. 数据库迁移（若本次含 [migration]）
.venv/bin/luban migrate

# 3. 重新初始化（若种子文件有变更）
.venv/bin/luban init

# 4. 依赖方向守卫（领地隔离是否被破坏）
.venv/bin/lint-imports --config importlinter.toml

# 5. 上游守卫自查（你的 fork 是否误改了上游追踪的领地）
python scripts/check_upstream_guard.py --base upstream/master

# 6. 全量测试 + 覆盖率门槛
.venv/bin/python -m pytest --cov=lubanagent --cov-report=json --cov-report=term-missing --cov-fail-under=70
.venv/bin/python scripts/check_coverage.py

# 7. 集成冒烟（Docker 全栈，本地跑——CI 不固化此步）
docker compose up -d                  # app + MySQL 8 + Langfuse + langfuse-db + mock-biz
```

逐项含义：

| 步骤 | 验证什么 | 失败意味着 |
|------|----------|-----------|
| `ruff check` | 代码风格与基础静态检查 | 合并引入格式/未用导入等问题，`ruff check --fix` 可自动修一部分 |
| `luban migrate` | 库表与代码 schema 一致 | 迁移未执行或冲突，回到第 4 节 |
| `luban init` | 种子数据可导入 | 种子文件格式变化，查 CHANGELOG `[scaffold-seed]` |
| `lint-imports` | 依赖方向契约（20 条）未被破坏 | 合并误引入了 `lubanagent → app` 等禁止方向，需拆分 |
| `check_upstream_guard` | 你的 fork 没改上游追踪的领地 | 你误改了 `lubanagent/`，应改为扩展点 |
| `pytest` | 功能与回归测试通过 | 合并引入回归，定位到具体测试 |
| `check_coverage.py` | 核心子包 ≥85%、整体 ≥70% | 合并后覆盖率下降，需补测试 |
| `docker compose up` | 全栈集成正常（本地跑，CI 不固化） | 配置/编排漂移，查 `docker-compose.yml` 合并结果 |

**边界**：CI 已固化前 6 步（`.github/workflows/ci.yml`）；第 7 步 docker compose 全栈冒烟请本地跑（CI 用 mysql service 实跑 alembic 覆盖 schema 层）。生产用 MySQL——上线前务必在 MySQL 环境复跑 `alembic upgrade head` + `pytest`。
