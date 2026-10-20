# 技术栈选型与可替换件

> 这一页说清楚 LubanAgent 每一层用了什么、为什么选它、哪些能换、怎么换。看完你就知道哪些是动不了的地基、哪些是拧个开关就能替的零件。

## 当前技术栈

| 层 | 选型 | 为什么选它 |
| --- | --- | --- |
| 语言 | Python ≥ 3.11 | Agent 生态主流语言；3.11 起的类型与性能够生产用 |
| Agent 引擎 | LangChain + LangGraph | 一个生态、同一套对象。普通 Agent 用 LangGraph prebuilt + 治理包装，确定性流程手写图 |
| 模型接入 | OpenAI 兼容协议 | 一套客户端覆盖 DeepSeek/Qwen/GLM/vLLM/Ollama；厂商原生 SDK 按需补 |
| 工具协议 | MCP（官方 Python SDK） | 行业标准，既能接别人的工具，也能把自己的工具给别人 |
| 能力包 | Agent Skills 开放标准（SKILL.md） | 行业标准，存量 Skill 包零适配可用 |
| 接入层 | FastAPI + SSE | 异步、自动出 OpenAPI 契约、流式推送运行事件 |
| 业务存储 | MySQL 8.0+（开发期 SQLite 免安装） | 成熟关系库，定义/版本/Trace/checkpoint 统一落库 |
| 观测后端 | Langfuse 自部署（默认） | 保数据主权、克隆即有监控 UI；可换 LangSmith 或关掉 |
| 前端 | React 18 + Vite + TypeScript + Tailwind + shadcn/ui | Langfuse 前端是 React 可借鉴、AI SDK 流式 hook 现成、shadcn 源码进仓库可控 |
| 部署 | Docker Compose | 克隆即跑，可选依赖按需编排 |

## 哪些是地基（不换）

这几项是技术栈前提，换等于换项目，不在"可替换"范围：

- **Python + LangChain + LangGraph**：整个执行层跑在 LangGraph 上，业务代码面向 LangChain 编程模型。换生态=重写。
- **FastAPI**：接入层与 OpenAPI 契约链绑死，前端 codegen 依赖它。
- **领地隔离结构**：`lubanagent/`（框架领地）与 `app/`（你的领地）物理隔离 + import-linter 守卫。这是"若依式基座"能跟上游升级的根本，不是技术选型而是架构契约（见[改造总览](./overview.md)）。

## 哪些是零件（可替换）

以下都是扩展点或配置切换，换不动核心：

| 零件 | 默认 | 可换成 | 怎么换 | 代价 |
| --- | --- | --- | --- | --- |
| 观测后端 | Langfuse 自部署 | LangSmith / 关闭（none） | 改 `OBSERVABILITY_BACKEND` env；LangSmith 注释掉 compose 里 langfuse 服务 | 低。核心七问由内部 DB 保证；LangGraph 节点级细粒度可视化 LangSmith 原生更细、Langfuse 取决于适配器映射 |
| 模型厂商 | OpenAI 兼容（DeepSeek/Qwen/GLM/vLLM/Ollama） | 任何 OpenAI 兼容服务；厂商原生 SDK | OpenAI 兼容：加一条 Model Definition + 改库映射，不改代码；原生 SDK：在 `lubanagent/model/` 加工厂代码 | 兼容协议零代码；原生 SDK 加一个工厂文件 |
| 业务数据库 | MySQL 8 | SQLite（开发期） | 改 `DATABASE_URL` env | 开发期免安装；生产用 MySQL |
| 向量库 | 不启用 | 任选 | 最小 RAG 默认不启用，需要时配置 | 简单 Agent 不引入额外组件 |
| RAG / 记忆 / 图编排 / 多 Agent | 不启用 | 按需启用 | 定义层配置 | 都是可选一等能力，默认不拖累简单 Agent |
| 鉴权 | API Key（可插拔） | 自定义 AuthProvider | 实现 `AuthProvider` Protocol 注册 | 可关，方便接业务网关 |
| 限流 | 内存实现 | Redis 等 | 实现 `RateLimiter` Protocol | 多业务系统共用时换分布式实现 |

具体替换操作（模型 / 观测 / 数据库 / 向量模型的完整命令）见[换模型与换后端](./swap-backends.md)。

## 三个最常做的替换

### 1. 换观测后端（最常被问）

```bash
# 换 LangSmith（云版）
OBSERVABILITY_BACKEND=langsmith
LANGSMITH_API_KEY=sk-...
# docker-compose.yml 注释掉 langfuse 与 langfuse-db 两个服务

# 完全不要外部后端（最轻量，只剩内部 DB）
OBSERVABILITY_BACKEND=none
```

核心功能不依赖任何外部后端——Trace 始终先写内部 DB（source of truth），外发是旁路，失败不阻断运行。

### 2. 换模型

```bash
# OpenAI 兼容：改库映射指向新标识即可，Run 立即走新模型，不改代码不发版
# 例：把 smart 槽位从 deepseek-chat 指向 qwen-max
curl -X PUT http://localhost:8000/v1/brain-mappings \
  -H "Content-Type: application/json" \
  -d '{"profile": "smart", "model_key": "qwen-max"}'
# 也可在开发者工作台改槽位映射
```

厂商原生 SDK（非 OpenAI 兼容）：在 `lubanagent/model/` 加一个工厂文件，注册为 Model Definition。新增厂商只动这一个模块，业务层零感知。

### 3. 关掉用不上的能力

简单 Agent 不需要 RAG、记忆、图编排、多 Agent——默认就是不启用。你的第一个 Agent 可以只配"人设 + 大脑 + 一个 Tool"，其余全是可选。

## 边界

- 技术栈前提（Python / LangChain / LangGraph / FastAPI / 领地结构）是地基，不做可替换承诺。
- 可替换零件的切换成本是"改配置 / 加一个扩展点实现"，不是"重写模块"——这是"无强制依赖、代码优先"价值主张的落地。
- 观测后端切换不等价：核心数据无损，但后端原生细粒度体验有差，我们诚实说明而非宣传"无缝切换"。
- 前端选型（React 全家桶）是硬选型，不在可替换零件表内——换 Vue/Next 会失去 Langfuse 组件借鉴、AI SDK 流式、shadcn 源码可控三层现成复用。
