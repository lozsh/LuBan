# Skill 能力包

> 本页讲 Agent Skills 开放标准（SKILL.md）、渐进披露三档加载、Skill / Tool / MCP 的分工、两种装载方式与分配到 Agent 的做法，以及治理边界。

## Skill 是什么（官方定义）

Agent Skills 是 Anthropic 2025 年 10 月发布、随后作为**开放标准**放开的格式（[agentskills.io](https://agentskills.io)，[Anthropic 公告](https://www.anthropic.com/news/skills)）。官方定义："一个轻量、开放的格式，用专业知识与工作流扩展 Agent 能力。核心是一个装有 SKILL.md 的文件夹——元数据（至少 name 和 description）+ 告诉 Agent 如何完成某类任务的指令；还可以打包脚本、参考资料、模板等资源。"

加载方式是**渐进披露（progressive disclosure）**三档：

1. **发现**：启动时只加载每个 Skill 的 name + description（约 100 token），够判断"什么时候用"；
2. **激活**：任务命中描述时，才把 SKILL.md 正文读进上下文（建议 < 5000 token / 500 行内）；
3. **执行**：正文引用的 scripts/、references/、assets/ 文件按需加载或执行。

生态事实（2026-07 核查）：官方示例库 [anthropics/skills](https://github.com/anthropics/skills) 162k★，规范仓库 [agentskills/agentskills](https://github.com/agentskills/agentskills) 23k★，已被 Claude 全家桶及越来越多的 Agent 产品采纳。

## Skill、Tool、MCP 的分工

| | Tool | Skill | MCP |
| --- | --- | --- | --- |
| 本质 | 可执行的动作（代码） | 知识与流程的包（指令+资源） | 连接协议（工具/数据的标准接口） |
| 回答 | "能做什么" | "什么时候做、按什么章法做" | "怎么和外部系统说话" |
| 例子 | `refund_apply(order_id, amount)` | 《退款处理 SOP》：什么情况能退、先查什么、超权限怎么走人工 | 接上 GitHub、接公司 CRM |

三者组合：Skill 的正文会指导模型何时调用哪些 Tool；Tool 可以来自本地，也可以来自 MCP。

> **注意**：Skill 是"指导"——模型大概率遵循，但不保证遵循。**必须按固定步骤执行的流程（退款、审批）不要交给 Skill 承载，用[图编排](./graphs.md)做代码级强制。**

## 怎么用

两种装载方式：

**1. 目录装载**：把符合标准的目录放进 `skills/`（示意）：

```
skills/refund-sop/
├── SKILL.md          # frontmatter: name, description；正文：退款 SOP 步骤
├── scripts/check_policy.py
└── references/policy.md
```

**2. zip 包上传**：把上述目录打成 zip，经工作台「Skills → 上传 Skill 包」或 HTTP 上传，自动解压到 `skills/{name}/` 并扫描入库：

```bash
curl -F "file=@refund-sop.zip" http://localhost:8000/v1/skills
```

zip 必须恰好含一个目录 + SKILL.md；同名 Skill 覆盖更新；zip 路径逃逸（zip slip）被拒绝。

**分配到 Agent** 两种方式：

- 工作台：Skills 列表点「分配」→ 勾选 Agent → 保存（每个变化的 Agent 升一个版本，`skills` 数组增删本 Skill）。
- 定义：在 Agent 定义的 `skills: [refund-sop]` 里登记（YAML 导入见 [Agent 定义](./agent-definition.md)）。

存量 Anthropic 生态的 Skill 包**直接可用**，不写一行适配代码。运行期 `skill_load` 工具按 Agent 分配强制加载——未分配的 Skill 调 `skill_load` 被 403 拒绝。

## 治理与边界

Skill 与定义同库版本化、可审计；scripts/ 里的代码在受控环境执行（超时强制终止、网络白名单标记传入子进程；进程级网络隔离需容器/网关兜底）。**支持本地目录装载与 zip 包上传**（工作台/HTTP）；**不做** Skill 公共市场与在线分发。
