# 跟上游升级

> LubanAgent 是"若依式起步基座"——你克隆后长期住进去迭代、上生产。这一页告诉你怎么定期把上游变更拉回来，而不丢掉自己的改动。

## 为什么能升级：领地契约

升级的底层保证是**领地三重隔离**（见[改造总览](./overview.md)）：

- `lubanagent/`——框架领地，上游追踪，你原则上不动；
- `app/`——你的领地，上游**永不主动触碰**（你新增的文件天然受保护）；
- `demos/` `tests/` `docs/` `migrations/` 等——共享领地，按需手工合并。

两条 CI 守卫维持边界：**import-linter** 拦依赖反向（`app → lubanagent` 允许、反向禁止）；**`.lubanagent-upstream` 守卫**拦截上游对 user-owned 路径的改动。只要你的业务改动住在自己的领地里，上游的 bug 修复、新能力、性能优化就能定期拉回而架构上保证不冲突。

## 升级步骤

一次性配置 upstream remote，之后每次升级走五步：

```bash
# 一次性：登记上游仓库（替换为真实上游地址）
git remote add upstream https://github.com/<org>/Luban.git

# ① 拉取并合并
git fetch upstream
git checkout master
git merge upstream/master        # 推荐保留合并节点；rebase 会改写你的提交哈希，有协作者时慎用

# ② 按 CHANGELOG 领地标签对号入座（见下表），执行对应合并动作

# ③ 按 UPGRADE.md 的"当前版本升级注意事项"执行合并指令（迁移、依赖、种子处理）

# ④ 跑测试
python -m pytest

# ⑤ 跑评测回归（确认模型/Prompt/流程行为没被升级带偏）
# 用升级前的评测基线 --compare 对比：
luban eval run customer-service-bot@<当前版本> --dataset <你的数据集> --compare <上一基线>
```

合并冲突主要发生在哪：

- **`lubanagent/`**：你若改过框架代码会冲突——优先采纳上游，把你的定制改为扩展点实现放进 `app/tools/` 或 `app/graphs/`（由 `EXTENSION_PACKAGES` 扫描），或在 `app/main.py` 手动注册；
- **`app/` 内种子文件**（`app/main.py`、客服示例 YAML 等）：上游仅做破坏性变更时才动，冲突时保留你的版本、只取破坏性修正；
- **`app/` 内你的文件**：上游永不主动触碰；
- **共享领地**：逐文件手工合并。

## CHANGELOG 领地标签：对号入座

CHANGELOG 每条变更按领地打标签，升级时按标签找对应合并动作：

| 标签 | 领地 | 你要做什么 |
| --- | --- | --- |
| `[framework]` | `lubanagent/` | 直接合并，通常无冲突（这是你跟上游的核心收益） |
| `[scaffold-seed]` | `app/` 内初始种子文件 | 保留你的改动，只取上游的破坏性修正 |
| `[shared]` | `docs/` `tests/` `docker/` `pyproject.toml` 等 | 手工合并，逐文件判断；合并后务必跑 `lint-imports` |
| `[migration]` | 数据库 schema | 合并后立即执行 `luban migrate`（迁移是累加的，不能跳版本） |
| `[breaking]` | 扩展点 Protocol / core 契约 | 按 UPGRADE.md 的迁移示例手工适配你的扩展实现，升 major 版本号 |

## .lubanagent-upstream 守卫的语义

仓库根的 `.lubanagent-upstream` 清单用 glob 列出四类路径：`[framework]`（上游追踪）、`[shared]`（共享）、`[scaffold-seed]`（种子）、`[user-owned]`（上游永不主动触碰）。CI 脚本比对上游 PR diff 是否命中 user-owned glob，命中即红——**你的业务代码不会因为上游合并而被动改写**。

必须诚实说明的边界：**守卫显著降低合并冲突，但不承诺任意 git merge 零冲突**（未登记的新路径、极端重名等仍可能冲突，合并后自查）。使用要点：

- 你在 `app/` 下新增业务文件时，通配规则已自动覆盖（如 `app/agents/` 下除种子 YAML 外全部 user-owned），无需改清单；
- 仅当你在 `app/` 外（如根目录）新增用户文件时，才需在 `[user-owned]` 补 glob；
- `user-owned` 与 `scaffold-seed` 重叠时以 `scaffold-seed` 为准（种子文件上游可动）；
- 守卫只防"上游主动改用户领地"，不防"用户改框架领地"——后者靠 import-linter 约束；
- 本地可自查（审上游 diff 是否越界）：

```bash
python scripts/check_upstream_guard.py --base upstream/master
```

## YAML 导出/导入在升级中的作用

Agent 定义的本体是**数据库记录**，YAML 只用于初始化导入与导出迁移——这在升级时正好是备份与迁移的载体：

- **升级前**：把现网 Agent 定义导出为 YAML 留底；
- **跨环境迁移**（如 staging → 生产）：导出的 YAML 一次性导入新环境。

注意：改 YAML 文件不会改变任何运行中的 Agent——必须重新导入才会生效。生产环境的定义编辑以工作台/API 为准，YAML 是迁移通道而不是存储。

## 合并后的验证清单

```bash
# 1. 代码风格
.venv/bin/ruff check lubanagent tests migrations app demos scripts
# 2. 数据库迁移（若本次含 [migration]）
.venv/bin/luban migrate
# 3. 重新初始化（若种子文件有变更）
.venv/bin/luban init
# 4. 依赖方向守卫（领地隔离是否被破坏）
.venv/bin/lint-imports --config importlinter.toml
# 5. 上游守卫自查
python scripts/check_upstream_guard.py --base upstream/master
# 6. 全量测试 + 覆盖率门槛
.venv/bin/python -m pytest --cov=lubanagent --cov-fail-under=70
# 7. Docker 全栈冒烟（本地跑）
docker compose up -d
```

**边界**：`[breaking]` 变更不保证向后兼容，升级前务必读 CHANGELOG 对应条目与 UPGRADE.md 的迁移示例；生产环境升级前先在 staging 验证。`alembic downgrade base` 会删表，生产环境务必先备份。
