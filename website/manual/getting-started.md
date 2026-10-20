# 快速开始

> 目标：从克隆仓库到看到 Agent 真的说话，最短路径。零外部密钥即可完成——SQLite + FakeLLM 兜底，验证的是**链路**；真智力见第 4 步接真模型。

## 前置

- Python ≥ 3.11（推荐 3.12）、[uv](https://docs.astral.sh/uv/)
- 跑全栈（含工作台前端）另需 Node.js ≥ 20

## 1. 安装并初始化

```bash
git clone <repo-url> LubanAgent && cd LubanAgent
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
cp app/config/.env.example .env   # 零配置也能起：SQLite + FakeLLM 兜底
.venv/bin/luban migrate           # 建表
.venv/bin/luban init              # 导入种子 Agent/Tool/知识源
```

## 2. 第一次对话

验证 Agent 真的能说话（无需模型 Key，FakeLLM 兜底——回复为固定台词）：

```bash
.venv/bin/uvicorn app.main:create_app --factory --port 8000 &
curl -N -X POST http://localhost:8000/v1/agents/customer-service-bot/runs \
  -H "Content-Type: application/json" \
  -d '{"input": "你好"}'
# SSE 事件流：run.started → token.delta… → run.completed
```

## 3. 起本地全栈（后端 API + 工作台前端）

```bash
scripts/dev.sh start    # 工作台 http://localhost:5173 ，API http://localhost:8000
                        # 首次运行自动 npm install 拉前端依赖（约 1 分钟），之后秒起
```

工作台 9 个管理页面 + 用户端聊天入口（`/chat`）怎么用，见[开发者工作台](./features/workbench.md)。

## 4. 接真实模型（两步）

`.env` 填厂商 API Key，再通过工作台"模型"页或 API 把模型写入库、绑到逻辑槽位（连接信息存数据库，换模型不改代码不发版）：

```bash
DEEPSEEK_API_KEY=sk-xxx        # 或 DASHSCOPE_API_KEY / ZHIPUAI_API_KEY / OPENAI_API_KEY
```

```bash
# 1. 注册模型（连接信息入库；单价字段用于费用归因，可不填）
curl -X PUT http://localhost:8000/v1/models -H "Content-Type: application/json" -d '{
  "key": "deepseek-chat", "provider": "deepseek", "model_name": "deepseek-chat",
  "factory": "openai_compat", "api_key_env": "DEEPSEEK_API_KEY",
  "base_url": "https://api.deepseek.com/v1",
  "capability": {"tool_calling": true},
  "params": {"input_price_per_1k": 0.002, "output_price_per_1k": 0.008}
}'
# 2. 绑到逻辑槽位（Agent 定义不动，新 Run 立即走新模型）
curl -X PUT http://localhost:8000/v1/brain-mappings -H "Content-Type: application/json" \
  -d '{"profile": "smart", "model_key": "deepseek-chat"}'
```

也支持任意 OpenAI 兼容服务（vLLM / Ollama 自建端点）：在 ModelDefinition 里显式指定 `base_url` 与 `api_key_env` 即可。

## 5. 一键全栈（Docker）

零配置起完整演示栈：app（含工作台 UI）+ MySQL 8 + Langfuse（观测）+ mock-biz（客服示例业务服务）。

```bash
docker compose up -d --build       # 首次约 3-5 分钟（含前端构建）
# 容器内 app 启动时自动 luban migrate && luban init（幂等，重起安全）
#   工作台+API   http://localhost:8000    （UI 由 FastAPI 同端口托管）
#   用户端聊天   http://localhost:8000/chat
#   Langfuse     http://localhost:3000
#   mock-biz     http://localhost:9100
```

生产环境部署清单见[部署与生产清单](./deploy.md)。

## 下一步

- 系统学会：[教程](../tutorial/index.md)（8 课从零到部署）
- 逐功能查字段与边界：左侧[功能手册](./features/agent-definition.md)
- 改造成自己的业务：[改造指南](./customization/overview.md)
