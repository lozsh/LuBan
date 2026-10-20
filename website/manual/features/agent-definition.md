# Agent 定义与版本化

> 本页讲 Agent 定义的构成、存库与版本化语义，以及 YAML 的正确用途。系统提示词的两种写法（内嵌 / 引用 Prompt 库）见 [Prompt 库](./prompt-library.md)。

**是什么**：一个 Agent 的身份（名称、描述、问候语、虚拟形象）、指令（系统 Prompt）、大脑（逻辑模型槽位）、知识（挂哪些知识库）、记忆（哪种策略）、能力（可用 Tool / MCP / Skill 清单）、行为边界（最大步数、预算、超时）、输入输出契约，全部写在一份**定义**里。**定义的本体是数据库记录**——不是 YAML、不是配置文件；每次修改在库内生成一个新版本。

**为什么需要**：改 Agent 行为 = 改数据库里的定义，不是改代码、不是发版；每次 Run 绑定明确的定义版本，出了问题能精确回溯"当时跑的是哪个版本"；审计与评测都建立在版本之上。

**怎么用**：

1. 定义存库、版本化在库内完成。首次创建可以走控制台 / API，也可以用 YAML **一次性导入**（仅初始化与迁移用，示意）：

```yaml
name: customer-service-bot
brain: smart                 # 逻辑大脑槽位
instruction: |
  你是售后客服，先安抚情绪，再查订单……
tools: [query_order, refund_apply]
mcp_servers: [github-mcp]    # 可选
skills: [refund-sop]         # 可选
knowledge: [product-faq]     # 可选
memory: session-summary      # 可选
limits: { max_steps: 20, budget_tokens: 200000, timeout_sec: 120 }
io_contract: { input: text, output: text }
```

   其中 `brain` 逻辑大脑槽位见[模型中心与模型工厂](./models.md)，`mcp_servers` 见 [Tool 与 MCP](./tools-mcp.md)，`skills` 见 [Skill 能力包](./skills.md)，`knowledge` 与 `memory` 见 [知识与记忆](./knowledge-memory.md)。

2. 保存即生成 `v1`；此后所有修改都在数据库里进行（控制台或 API），每次保存生成 `v2`、`v3`……支持版本 diff 与回滚。
3. YAML 只有两种用途：**初始化导入**和**导出备份/跨环境迁移**。它不是存储——改 YAML 文件不会改变任何运行中的 Agent，必须重新导入才会生效。
4. 会话/Run 引用定义版本；未指定则用当前发布版本。

**边界**：不做可视化拖拽设计器；定义的在线编辑在[开发者工作台](./workbench.md)。
