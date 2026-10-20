# 换模型与换后端

> 这一页收录四个"最常做的替换"：换模型、换观测后端、换数据库、换向量模型。共同点：都是**改配置或改库记录**，不是改代码——哪些能换哪些是地基的完整清单见[技术栈选型与可替换件](./tech-stack.md)。

## 换模型（三步，不改代码不发版）

Agent 从不直接绑定具体模型，而是绑定逻辑大脑槽位（fast / smart / cheap / vision）。切换模型 = 改库里的映射指向另一个固定标识。

**第 1 步：注册模型**（连接信息入库；单价字段用于费用归因，可不填）：

```bash
curl -X PUT http://localhost:8000/v1/models -H "Content-Type: application/json" -d '{
  "key": "deepseek-chat", "provider": "deepseek", "model_name": "deepseek-chat",
  "factory": "openai_compat", "api_key_env": "DEEPSEEK_API_KEY",
  "base_url": "https://api.deepseek.com/v1",
  "capability": {"tool_calling": true},
  "params": {"input_price_per_1k": 0.002, "output_price_per_1k": 0.008}
}'
```

**第 2 步：绑到逻辑槽位**：

```bash
curl -X PUT http://localhost:8000/v1/brain-mappings -H "Content-Type: application/json" \
  -d '{"profile": "smart", "model_key": "deepseek-chat"}'
```

**第 3 步：立即生效**——Agent 定义不动，新 Run 立即走新模型；历史 Run 的 Trace 里仍如实记录着旧模型。也可在开发者工作台「模型」页改槽位映射。

全部走 OpenAI 兼容协议：DeepSeek / Qwen / GLM 等国内厂商与 vLLM、Ollama 私有化部署一个客户端全覆盖——vLLM / Ollama 自建端点在 ModelDefinition 里显式指定 `base_url` 与 `api_key_env` 即可。

**边界**：厂商原生 SDK（非 OpenAI 兼容）需要在 `lubanagent/model/` 加一个工厂文件——这是框架领地，改了会与上游冲突（新增厂商的工厂代码通常值得向上游贡献而不是自己维护）。

## 换观测后端

```bash
# 换 LangSmith（云版）
OBSERVABILITY_BACKEND=langsmith
LANGSMITH_API_KEY=sk-...
# docker-compose.yml 注释掉 langfuse 与 langfuse-db 两个服务

# 完全不要外部后端（最轻量，只剩内部 DB）
OBSERVABILITY_BACKEND=none
```

默认是 Langfuse 自部署（保数据主权、克隆即有监控 UI）。核心功能不依赖任何外部后端——**Trace 始终先写内部 DB（source of truth），外发是旁路，失败不阻断运行**。切换后端不等价：核心数据无损，但后端原生细粒度体验有差。

## 换数据库

| 场景 | 数据库 | 配置方式 |
|------|--------|----------|
| 开发期 | SQLite（免安装，零配置） | `.env` 不设 `DATABASE_URL` 即默认 SQLite，`luban migrate` 自动建库 |
| 生产期 | MySQL 8.0+ | `.env` 设 `DATABASE_URL=mysql+asyncmy://user:pass@host:3306/lubanagent`，先建库再 `luban migrate` |

MySQL 驱动需装可选依赖：`uv pip install -e ".[mysql]"`。迁移命令：`luban migrate`（等价 `alembic upgrade head`），回滚 `alembic downgrade -1`。迁移链不能跳版本。

## 换向量模型

知识库的向量化模型用环境变量配置：

```bash
EMBEDDING_MODEL=...                      # 向量模型名
EMBEDDING_BASE_URL=...                   # 可选，OpenAI 兼容端点
EMBEDDING_API_KEY_ENV=...                # 可选，存放密钥的环境变量名
```

**不配时用内置的确定性演示向量**——够跑通示例，不承诺生产检索质量。换用真实 embedding 模型后注意两件事：

1. **重新灌库**（维度与语义都变了，存量向量失效）；
2. **重调阈值**——防错答守门的 `score_threshold` 分数量纲随向量模型不同，可用工作台知识库页面的"检索测试"面板实测校准。

## 边界

- 地基（Python / LangChain / LangGraph / FastAPI / 领地结构）不做可替换承诺，见[技术栈选型与可替换件](./tech-stack.md)。
- 可替换零件的切换成本是"改配置 / 加一个扩展点实现"，不是"重写模块"。
- 换模型、换观测后端都是运行期数据操作，不需要发版；换数据库、换向量模型涉及迁移与重灌，按上面步骤操作。
