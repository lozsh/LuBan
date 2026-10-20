# 契约 D1-9：CI 与测试基建收尾 + 交付件（docker-compose / upstream 清单）

> 来源：开发计划 D1-9、架构设计 §3.2/§4.4/§6.5、AGENTS.md §4（升级契约）。
> 前置：全部开发单元完成（本单元是收口）。FakeLLM + SQLite + noop 观测兜底使 CI 零外部密钥全绿。

## 范围

- `.github/workflows/ci.yml`
- `scripts/check_upstream_guard.py`（.lubanagener-upstream 校验）
- `.lubanagent-upstream`（清单文件，架构 §4.4 已定内容）
- `docker-compose.yml` + `docker/Dockerfile` + `.dockerignore（仓库根；build context 是仓库根，故必须在根）`
- `UPGRADE.md` + `CHANGELOG.md`（首版）
- pyproject 覆盖率门槛配置（pytest-cov）

## 接口契约

### ci.yml（GitHub Actions，push/PR 触发）
```yaml
jobs:
  backend:
    steps:
      - uv 安装 Python 3.12 + uv pip install -e ".[dev]"
      - ruff check lubanagent tests migrations app demos
      - lint-imports --config importlinter.toml
      - alembic upgrade head（SQLite 临时文件冒烟）+ downgrade base + upgrade head
      - pytest --cov=lubanagent --cov-report=term-missing --cov-fail-under=70
           --cov-config=pyproject.toml（核心模块 core/extension/config/model/runtime/tool ≥85% 用
           coverage 的 fail_under 分项做不到 → 用 scripts/check_coverage.py 分项断言，见下）
      - luban --help 冒烟；luban migrate + init + eval 冒烟（tmp HOME）
  frontend:
    steps:
      - node 22 + npm ci（lubanagent/ui）
      - npm run codegen:check（openapi.snapshot.json 一致性）
      - npm run typecheck && npm run lint && npm run test && npm run build
  upstream-guard:
    steps: python scripts/check_upstream_guard.py   # 见下
```

### scripts/check_coverage.py
```python
# 读 coverage xml/json 报告：整体 ≥70%；核心子包（core/extension/config/model/runtime/tool/
# persistence/observability）各 ≥85%——不达标 exit 1 并打印缺口清单
```

### scripts/check_upstream_guard.py
```python
# .lubanagent-upstream 清单解析（[framework]/[shared]/[scaffold-seed]/[user-owned] 四节 glob）
# 校验：当前 HEAD 与 origin/master（或 PR base）diff 中，上游维护的提交不触碰 [user-owned] glob
# （本地自校验模式：git diff --name-only HEAD~1 全部命中 user-owned → 报警提示，exit 1）
# 零 git 上下文时（无 origin）退化为清单格式自检（glob 合法、四节齐全、user-owned 非空）
```

### docker-compose.yml（架构 §6.5 形态）
- `app`：Dockerfile 构建（python:3.12-slim + uv pip install + uvicorn）；env：DATABASE_URL=mysql+asyncmy、OBSERVABILITY_BACKEND=langfuse、BIZ_BASE_URL=http://mock-biz:9100；depends_on: mysql/langfuse/mock-biz；ports 8000
- `mysql:8`（healthcheck + volume + 初始 lubanagent 库）
- `langfuse` + `langfuse-db`(postgres:16)（§6.5 形态，key 缺省空——app 缺 key 自动降级 noop 不阻断）
- `mock-biz`：python:3.12-slim 跑 `python -m demos.customer_service.mock_services`（D5-1）
- **决策**：compose 里 app 启动命令 = `sh -c "luban migrate && luban init && uvicorn app.main:app"`（migrate/init 幂等，重起安全）

### docker/Dockerfile
多阶段：builder（uv pip install 到 venv）→ runtime（copia venv + 仓库代码，非 root 用户，EXPOSE 8000）。

### .lubanagent-upstream
按架构 §4.4 原文四节（framework/shared/scaffold-seed/user-owned）。

### CHANGELOG.md / UPGRADE.md
- CHANGELOG 首版 0.1.0：按 [framework]/[scaffold-seed]/[shared]/[migration] 分类记录首批交付。
- UPGRADE.md：升级工作流（git merge upstream/master → 冲突只在 scaffold-seed → alembic upgrade → pip install -e .）+ 版本兼容承诺（只加字段、breaking 升 major）。

## 行为契约

1. **CI 零外部密钥全绿**（开发计划验收原文）：FakeLLM + SQLite + noop 观测 + mock 渠道兜底，无 LANGFUSE/MySQL/真实 API key 也能全绿。
2. **故意越界即红**：`lubanagent → app` 的 import 让 CI 红（本地已验证，CI 步骤固化）。
3. **覆盖率门槛生效**：核心 ≥85%、整体 ≥70%（scripts/check_coverage.py 断言，故意删测试能看到红——本单元自验时临时演示后还原）。
4. **compose 配置静态有效**：`docker compose config -q` 通过（本机无 docker 则以 config 静态校验 + README 说明代替 up 实测）。

## 边界

- CI 不引入需要真实外部服务的步骤（MySQL 的 alembic 验收用 services: mysql:8 容器跑——GitHub Actions services 支持，**决策**：CI 加一个 mysql service job 跑 `alembic upgrade head`（asyncmy），兑现 D1-1 验收 6 的 MySQL 实跑）。
- 不宣传 Langfuse/LangSmith 完全等价（§6.6）。

## 验收标准（须实跑）

1. `bash` 级本地复演 CI 全流程（等同 ci.yml 各步骤逐条本地跑通）：ruff/lint-imports/alembic 三向迁移/pytest+覆盖率达标/luban 冒烟/前端四件套（若前端已完成）。
2. `scripts/check_coverage.py` 阈值断言真生效（阈值临时调高到当前值之上 → exit 1 → 还原）。
3. `scripts/check_upstream_guard.py`：格式自检 + 模拟 diff 触碰 user-owned → exit 1。
4. `docker compose config -q` 通过；Dockerfile 静态审查（多阶段、非 root）。
5. CHANGELOG/UPGRADE/.lubanagent-upstream 内容核验（四节 glob 与架构 §4.4 一致）。

## 存疑项与决策

1. MySQL 实跑放 CI services（本地无 MySQL 环境，决策见上）；docker compose up 实测同样归 CI/手动（本机无 docker daemon 时以静态校验代）。
2. 覆盖率门槛用脚本分项断言（coverage 原生不支持子包分项 fail_under）。
