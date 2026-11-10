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

1. **开启鉴权**：生产必须 `AUTH_ENABLED=true`。要在无 `API_KEY` 时用数据库账号，设 `AUTH_DATABASE_ENABLED=true`；首次空库引导再设置 12–256 位 `LUBAN_ADMIN_PASSWORD`。`API_KEY` 仍可作为破窗引导身份。仅当鉴权开启、无 `API_KEY`、无自定义 AuthProvider 且 `AUTH_DATABASE_ENABLED=false` 时，服务启动期 fail fast；数据库账号模式下若无活跃 admin、又无 API Key/provider 且未设 `LUBAN_ADMIN_PASSWORD`，首次启动同样 `CONFIG_ERROR`，已有活跃 admin 重启不需要引导密码。
2. **执行迁移并管理账号**：先跑 `luban migrate` 建 `application_logs` 与用户、角色、权限、会话等安全表；登录后按最小权限分配角色，保留至少一个启用中的 admin。令牌默认 8 小时（`AUTH_SESSION_HOURS`），只存哈希；禁用、改密和退出会撤销既有会话，角色/权限变化不撤销、经每请求重载实时生效。
3. **改 Langfuse 默认密码**，或直接切 `OBSERVABILITY_BACKEND=langsmith / none`（内部 DB 始终是 source of truth，见[运行观测](./features/observability.md)）。
4. **真实 MySQL 凭证**：改 `DATABASE_URL`，不要用 compose 示例里的默认账号密码。
5. **持久卷**：MySQL 数据卷、上传文件目录（`data/`，含知识库文件与头像，env `AVATAR_FILES_ROOT` 可改）必须挂持久卷。
6. **环境分层**：dev/staging/prod 用 `.env` 分层覆盖；密钥走你的密钥管理，不进镜像不进仓库。
7. **可信网关**：自定义 AuthProvider 的服务端 scopes 是唯一可信权限；不要通过 header 或请求体声明角色。`user_id` 仍是业务侧排查标签，不是平台账号外键；平台 RBAC 不做租户、部门或行级授权。

完整的逐项操作与升级须知见 [docs/UPGRADE.md](https://github.com/lozsh/LuBan/blob/master/docs/UPGRADE.md)。

## 升级运维

跟上游版本走 `git merge` + CHANGELOG 领地标签（`[framework]` / `[scaffold-seed]` / `[shared]` / `[migration]` / `[breaking]`）对号入座，流程见[跟上游升级](./customization/upgrade.md)。

## 监控与排障

- **排一次对话的问题**：工作台 Traces 页或 `/diagnostics` 页按会话 ID / `user_id` / Run / Trace / `X-Request-ID` 关联过滤；执行树详情包含会话输入输出、Trace 事件与关联代码日志。日志默认保留 30 天，队列满或数据库写入失败可能丢批次，接口会暴露 `dropped_count` 和 `write_failures`。
- **成本**：[成本治理](./features/cost.md)的费用报表按 Agent/会话/天归集；调试台费用为 0 多半是模型注册没填单价字段。
- **质量回归**：上线前跑[评测](./features/evals.md)对比基线，`--compare` 有回归时 CLI exit 1，可挂 CI 门禁。
