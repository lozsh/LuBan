# CLI 命令参考

> `luban` 命令行是初始化 / 迁移 / 导入导出 / 评测入口，不承担运行期职责。命令：`luban init`、`luban export`、`luban migrate`、`luban eval run`。首次安装见[快速开始](../getting-started.md)。

所有命令的 `--database-url` 选项均可用环境变量 `DATABASE_URL` 代替；两者都缺省时读取 `Settings.load()` 的配置（`.env` 里的 `DATABASE_URL`，默认 SQLite）。

## luban init

**用途**：种子落库（内置默认数据，幂等）+ 扫描 `agents_dir` 下的 `*.yaml`（按文件名排序）导入 Agent 定义。重复调用安全。

**导入幂等语义**：

- Agent 不存在 → 创建（version=1）；
- 已存在且内容有变化（剔除 `version`/`status` 运行期字段后比对）→ 追加新版本（version+1）；
- 已存在且内容与当前版本一致 → 跳过，输出追加 `(unchanged)` 标记；
- `--fill-missing`：已存在一律跳过、不追加版本（dev.sh 自动启动用，避免磁盘旧 YAML 把工作台修改打回）。

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--agents-dir` | Agent YAML 目录 | `app/agents` |
| `--database-url` | 数据库 URL（也可用 env `DATABASE_URL`） | Settings 配置 |
| `--fill-missing` | 只导入不存在的 Agent，已存在一律跳过 | 关闭 |

```bash
.venv/bin/luban init
# 输出：每个 Agent 一行 key@version，未变化加 (unchanged)
# customer-service-bot@3 (unchanged)
# lib-assistant@2
```

## luban export

**用途**：把 Agent 定义导出为 YAML——初始化导入 / 导出迁移的双轨回程。往返无损：导出的 YAML 可直接用于 `luban init` 再导入。

| 参数 | 说明 | 默认值 |
|---|---|---|
| `key`（位置参数） | Agent key | 必填 |
| `--output` | 输出文件路径 | 缺省打印到 stdout |
| `--version` | 版本号 | 缺省当前版 |
| `--database-url` | 数据库 URL（也可用 env `DATABASE_URL`） | Settings 配置 |

Agent 或版本不存在时以错误码 `AGENT_NOT_FOUND` / `AGENT_VERSION_NOT_FOUND` 退出（exit 1）。

```bash
# 导出当前版到文件
.venv/bin/luban export customer-service-bot --output backup/cs-bot.yaml
# 导出指定版本并打印到终端
.venv/bin/luban export customer-service-bot --version 2
```

## luban migrate

**用途**：执行 Alembic 数据库迁移（`upgrade head`），建表 / 升级表结构。读仓库根 `alembic.ini`，迁移脚本在 `migrations/`。

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--database-url` | 数据库 URL（也可用 env `DATABASE_URL`） | Settings 配置 |

```bash
.venv/bin/luban migrate
# 输出：migrated to head
```

Docker 全栈容器内启动时自动执行 `luban migrate && luban init`（均幂等，重起安全）。

## luban eval run

**用途**：跑评测数据集，逐 case 输出 PASS/FAIL 与 `pass_rate`；报告落库（供 `GET /v1/evals` 读取）。回归门禁语义见[评测](../features/evals.md)。

| 参数 | 说明 | 默认值 |
|---|---|---|
| `agent`（位置参数） | Agent 规格：`key`（当前版）或 `key@version`（指定版本）；`@` 后非整数报 `VALIDATION_ERROR` | 必填 |
| `--dataset` | 评测数据集 YAML 路径 | 必填 |
| `--compare` | 对比对象：基线版本号（纯数字）或基线报告 JSON 文件路径 | 无 |
| `--save` | 本次报告 JSON 输出路径（可作下次 `--compare` 基线） | 无 |
| `--auto-approve` | 自动放行审批型工具：高风险工具真实执行；缺省不执行（工具调用意图仍会被 `expect_tools` 断言捕获） | 关闭 |
| `--database-url` | 数据库 URL（也可用 env `DATABASE_URL`） | Settings 配置 |

**退出码语义**：

- `0`：正常结束（含评测跑完但无回归）；
- `1`：`--compare` 命中回归（有 case 从 PASS 变 FAIL 或 pass_rate 下降）；
- `2`：运行错误（Agent/版本不存在、数据集加载失败、执行异常等，stderr 输出 `error: <错误码>: <消息>`）。

**输出格式**：逐 case 一行 `[PASS]`/`[FAIL] <case名>`（FAIL 附失败原因），末尾 `pass_rate: 0.83 (5/6)`；`--compare` 时追加 `regressions` / `improvements` / `pass_rate_delta` 三行。

```bash
# 跑数据集并保存报告
.venv/bin/luban eval run customer-service-bot --dataset datasets/cs_eval.yaml --save reports/baseline.json

# 与 v2 版本对比，有回归则 CI 失败（exit 1）
.venv/bin/luban eval run customer-service-bot@3 --dataset datasets/cs_eval.yaml --compare 2

# 与基线报告 JSON 对比
.venv/bin/luban eval run customer-service-bot@3 --dataset datasets/cs_eval.yaml --compare reports/baseline.json
```

评测数据集格式、断言与 judge 用法见[评测](../features/evals.md)；Agent YAML 字段见 [Agent 定义与版本](../features/agent-definition.md)。
