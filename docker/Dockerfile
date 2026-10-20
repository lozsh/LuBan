# syntax=docker/dockerfile:1
# LubanAgent 运行时镜像：三阶段构建（ui-builder 打工作台前端 → builder 装依赖 → runtime 只带 venv + 源码 + UI）

# 工作台前端：生产构建默认同源相对路径（client.ts 的 VITE_API_BASE 缺省空串），
# 产物 dist 由 runtime 阶段 FastAPI 同端口托管（factory.py ui_dist 逻辑）
FROM node:22-slim AS ui-builder
WORKDIR /ui
# 依赖层单独拷贝，源码变更不击穿缓存
COPY lubanagent/ui/package.json lubanagent/ui/package-lock.json ./
RUN npm ci
COPY lubanagent/ui ./
RUN npm run build

FROM python:3.12-slim AS builder
WORKDIR /app
# uv 版本固定（与生锁的 uv 0.6.x 系列同源，验证过再升）
COPY --from=ghcr.io/astral-sh/uv:0.6.7 /uv /usr/local/bin/uv
# 依赖层单独拷贝，源码变更不击穿缓存；uv.lock 冻结安装（H14：--frozen 不同步即失败）
COPY pyproject.toml uv.lock README.md ./
COPY lubanagent ./lubanagent
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --frozen --no-dev --extra mysql --extra langfuse --extra docs

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# 运行所需：包本体 + 用户领地种子 + 迁移 + CLI 配置 + 示例（mock-biz 数据）
COPY lubanagent ./lubanagent
# 工作台 UI（ui-builder 阶段产物；存在时 factory.py 自动挂载 SPA，与 API 同端口）
COPY --from=ui-builder /ui/dist ./lubanagent/ui/dist
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY demos ./demos
RUN useradd --create-home luban && chown -R luban:luban /app \
    && mkdir -p /data/knowledge /data/avatars && chown -R luban:luban /data
USER luban
EXPOSE 8000
# migrate 幂等；init --fill-missing 自动启动只补缺失 Agent，容器重建不打回工作台修改（H08）
CMD ["sh", "-c", "luban migrate && luban init --fill-missing && uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000"]
