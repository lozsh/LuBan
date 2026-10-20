# 改造总览

> LubanAgent 不是"搭完就扔"的原型：你克隆仓库后**长期住进去**迭代、上生产。这一页回答三个问题——往哪里改、怎么改、为什么改完还能跟上游升级。

## 改造心态：你不是"用"它，是"住"进去

LubanAgent 是"若依式起步基座"（类比 Java 圈的若依 / Spring Boot 脚手架之于业务系统）：框架把每个团队都要重拼的工程件——定义入库版本化、模型工厂、治理层、工作台、部署——都做好了；你的业务 Agent / 工具 / 流程作为住户住进 `app/` 领地。**你改的是自己的领地，框架领地原则上不动**——这是"改了很多还能跟上游升级"的根本前提。

## 三领地：你住哪里，框架住哪里

```text
lubanagent/   框架领地 —— 上游追踪，你原则上不动
app/          你的领地 —— 你的 Agent / Tool / 流程 / 扩展都放这里，上游永不主动触碰
demos/ tests/ docs/ migrations/   共享领地
```

目录与模块的详细对应关系（框架每个子目录放什么、`app/` 的约定扫描入口）见[项目架构](../architecture.md)。

## "改什么 / 怎么改"路由表

| 你想做什么 | 去哪一页 |
| --- | --- |
| 写业务工具（Tool）或知识流程包（Skill） | [写 Tool 与 Skill](./tools-skills.md) |
| 把必须按固定步骤走的流程固化（图编排），或多 Agent 协作 | [写图编排与团队](./graphs-teams.md) |
| 换模型 / 换观测后端 / 换数据库 / 换向量模型 | [换模型与换后端](./swap-backends.md) |
| 让自己的业务系统调用 Agent、接审批回调、反向暴露 MCP | [接入业务系统](./business-integration.md) |
| 弄清技术栈哪些是动不了的地基、哪些是拧开关就能替的零件 | [技术栈选型与可替换件](./tech-stack.md) |
| 跟上游升级、不丢自己的改动 | [跟上游升级](./upgrade.md) |
| 用规格驱动的方式开发自己的 Agent | [用 spec-coding 开发你的 Agent](./spec-coding.md) |

## 依赖方向规则：一条铁律 + 两条 CI 守卫

**铁律**：依赖方向单向——`app/* → lubanagent/*` **允许**；`lubanagent/* → app/*` **禁止**。框架从不反向 import 你的业务代码。

两条 CI 守卫保证这条铁律与领地边界不被破坏：

1. **import-linter**（`importlinter.toml`）：静态检查依赖方向，CI 里拦下任何 `lubanagent → app` 的禁止方向。
2. **`.lubanagent-upstream` 守卫**（`scripts/check_upstream_guard.py`）：仓库根的 `.lubanagent-upstream` 清单用 glob 列出框架 / 共享 / 种子 / user-owned 四类路径；上游 PR 的 diff 命中 user-owned 路径时 CI 拦截——上游永不主动改你的领地。

守卫**显著降低合并冲突，但不承诺任意 git merge 零冲突**（未登记的新路径、极端重名等仍可能冲突，合并后自查）。升级的完整流程见[跟上游升级](./upgrade.md)。

## import 即注册：框架怎么找到你的扩展

框架运行期**通过 Registry 找用户扩展，不反向 import**——你只要让框架扫描到你的模块，装饰即注册：

```python
from lubanagent.extension import tool, graph

@tool(description="查询订单状态与物流", risk="low", idempotent=True, timeout_seconds=10)
def query_order(order_id: str) -> dict: ...

@graph(name="refund_flow", description="退款流水线")
def refund_flow_builder():
    ...
```

- `EXTENSION_PACKAGES` 默认扫描 `app.tools` 与 `app.graphs` 两个包（可加环境变量扩展）；
- 模块被 import 时，`@tool` / `@graph` 装饰器把工具与图注册进 `default_registry`，Agent 定义里按 `key` / `name` 引用即可；
- 这就是"import 即注册"：**你的文件放对目录、写对装饰器，框架自然发现**——不需要改框架代码，也不需要配置文件登记。

## 边界

- 框架领地（`lubanagent/`）你原则上不动；发现必须改框架才能满足需求，多半是缺一个扩展点——向上游提 issue 而非就地改。
- `app/` 下的脚手架种子文件（`app/main.py`、客服示例 YAML 等）上游仅做破坏性变更时才动——**建议复制成新文件再改**，保持零冲突（清单见 `.lubanagent-upstream`）。
- 客服示例是演示"如何用框架能力组装一个完整业务场景"的活文档，不是产品本身。
