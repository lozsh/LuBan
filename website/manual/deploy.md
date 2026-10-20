# 部署与生产清单

> 开发期怎么跑见[快速开始](./getting-started.md)。这一页讲部署形态与上生产前必须做的事。

## 三种部署形态

| 形态 | 命令 | 适用 |
| --- | --- | --- |
| 本地开发全栈 | `scripts/dev.sh start` | 日常开发：API :8000 + 工作台 :5173 热更新 |
| Docker 单机全栈 | `docker compose up -d --build` | 演示/自托管：app+UI、MySQL、Langfuse、mock-biz 五服务一键起 |
| 生产部署 | 同上 + 生产清单改造 | 容器编排进你的基础设施（K8s 把 compose 服务逐个搬） |

镜像内三阶段构建（node 打工作台前端 → venv → 运行时），UI 与 API 同端口由 FastAPI 托管。容器内 app 启动时自动执行 `luban migrate && luban init`，两者幂等，重起安全。

## 生产清单（上线前逐条过）

1. **开启鉴权**：`AUTH_ENABLED=true` 并配置 `API_KEY`。不开启时 API 裸奔，只允许在内网/本机玩。
2. **改 Langfuse 默认密码**，或直接切 `OBSERVABILITY_BACKEND=langsmith / none`（内部 DB 始终是 source of truth，见[运行观测](./features/observability.md)）。
3. **真实 MySQL 凭证**：改 `DATABASE_URL`，不要用 compose 示例里的默认账号密码。
4. **持久卷**：MySQL 数据卷、上传文件目录（`data/`，含知识库文件与头像，env `AVATAR_FILES_ROOT` 可改）必须挂持久卷。
5. **环境分层**：dev/staging/prod 用 `.env` 分层覆盖；密钥走你的密钥管理，不进镜像不进仓库。
6. **可信网关**：走网关统一注入身份时，工作台无需填 Key；用户端生产入口由你的业务系统自建，走 REST/SSE 调用 LubanAgent（`user_id` 透传做观测标签，引擎不做用户管理，见 [Agent Runtime](./features/runtime.md)）。

完整的逐项操作与升级须知见 [docs/UPGRADE.md](https://github.com/lozsh/LuBan/blob/master/docs/UPGRADE.md)。

## 升级运维

跟上游版本走 `git merge` + CHANGELOG 领地标签（`[framework]` / `[scaffold-seed]` / `[shared]` / `[migration]` / `[breaking]`）对号入座，流程见[跟上游升级](./customization/upgrade.md)。

## 监控与排障

- **排一次对话的问题**：工作台 Traces 页按会话 ID / `user_id` 标签过滤，Trace 详情有完整现场（模型、工具调用、Token/费用、结束原因）。
- **成本**：[成本治理](./features/cost.md)的费用报表按 Agent/会话/天归集；调试台费用为 0 多半是模型注册没填单价字段。
- **质量回归**：上线前跑[评测](./features/evals.md)对比基线，`--compare` 有回归时 CLI exit 1，可挂 CI 门禁。
