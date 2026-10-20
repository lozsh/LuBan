# 贡献指南

> 这是什么：LubanAgent 的贡献流程约定。更新日期：2026-09-07。对应代码模块：全仓库。

感谢关注 LubanAgent！这是一个"用户克隆后长期住进去"的 Agent 工程基座，贡献前请先理解[仓库的三块领地](README.md#仓库布局你住哪里框架住哪里)——它决定了哪类改动会被接受、放哪里。

## 快速上手（5 分钟）

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp app/config/.env.example .env      # 零配置：SQLite + FakeLLM 兜底
.venv/bin/luban migrate && .venv/bin/luban init
.venv/bin/python -m pytest           # 全量测试，零外部密钥
```

前端（改 `lubanagent/ui` 时）：

```bash
cd lubanagent/ui && npm ci
npm run codegen:check   # OpenAPI 契约校验，改了后端 API 必须先同步快照
npm run typecheck && npm run lint && npm run test && npm run build
```

## 提交前自查（CI 全部会跑，先在本地过一遍）

```bash
.venv/bin/ruff check lubanagent tests migrations app demos scripts
.venv/bin/lint-imports --config importlinter.toml     # 架构契约守卫
.venv/bin/python -m pytest --cov --cov-fail-under=70   # 覆盖率整体 ≥70%
.venv/bin/python scripts/check_coverage.py             # 核心子包分项 ≥85%
```

改了公开 API 还要更新 `openapi.snapshot.json`（前后端契约同源的基线，漂移在 CI 拦截）；改了 `app/` 下用户文件要先看 `.lubanagent-upstream` 清单——user-owned 区的文件上游不接受改动。

## 什么样的贡献会被接受

| 领地 | 接受的改动 |
| --- | --- |
| `lubanagent/`（框架领地） | bug 修复、新框架能力（带测试与文档）、性能优化、依赖升级 |
| `docs/`（共享领地） | 文档修正、示例补充、翻译 |
| `app/`（用户领地种子） | 只有演示性质的改进会被考虑；它是给用户抄的样板，不是你的业务代码 |
| `demos/` | 客服示例场景的扩展 |

**不在范围内**（会被拒绝）：在线托管 SaaS、低代码设计器、模型/Prompt 商城、通用后台管理、自建观测平台、IM/电商渠道接入（属商用扩展层）。详见 [AGENTS.md](AGENTS.md) §3。

## 架构硬约束（违反必被 CI 打回）

1. **依赖方向单向**：`app → lubanagent` 允许，反向禁止（框架运行期通过 Registry 找用户扩展）。
2. **Agent 定义是数据库记录**，YAML 仅用于初始化导入/导出迁移，不是存储。
3. **模型经四槽位解耦**（fast/smart/cheap/vision），不直接绑定具体模型名。
4. **确定性规则由代码执行**（权限/金额/审批），不靠 Prompt 声明。
5. **核心不依赖渠道层**，扩展只走 `ChannelConnector` / `SessionStateStore` / `OutputGuard` 三个扩展点。

## 提 PR 流程

1. Fork → 分支（`feat/xxx` / `fix/xxx`）→ 改动 → 本地过完上面自查清单。
2. 提交信息用 conventional commits（中文描述可）：`feat: xxx` / `fix: xxx` / `docs: xxx`。
3. 改动若触及接口/数据/行为/边界，同步更新受影响的文档（用户手册 / 模块规格 `docs/specs/` / CHANGELOG，按领地打标签）。
4. PR 描述写清：改了什么、为什么、怎么验证的（测试输出/截图）。
5. CI 全绿后等待 review；涉"框架领地"的改动会额外对照 [模块规格](docs/specs/index.md) 审一致性。

## 报 bug / 提需求

用 issue 模板（bug report / feature request），bug 请附：复现步骤、期望 vs 实际、`luban doctor` 或日志输出、版本号（`pip show lubanagent` 或 git commit）。

## 开发方法论（选读）

仓库内置 [spec-coding](.claude/skills/spec-coding/) skill——意图 → 契约 → 编码 → 独立严审的规格驱动闭环。AI 辅助贡献者建议用它：先把改动意图落成可验证契约再动手。人类贡献者不强制，但 PR 描述里的"契约"意识（改了什么接口、影响什么行为）会大幅加速 review。
