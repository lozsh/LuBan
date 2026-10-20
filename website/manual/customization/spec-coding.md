# 用 spec-coding 开发你的 Agent

> LubanAgent 不只给你运行骨架，还给你开发自己 Agent 的工程纪律：仓库内置 spec-coding skill（`.claude/skills/spec-coding/`，克隆即用）——一套规格驱动开发闭环。

## 是什么

spec-coding 是规格驱动的开发方法：把开发意图落成**可验证的契约**，按契约编码，再由独立 reviewer 严格校验「代码 vs 契约」与「文档 vs 代码事实」，直至 PASS。skill 无内部依赖、语言无关——你开发自己的 Agent 同样可以用它。

## 为什么

Agent 开发容易"写完不知道对不对"：Prompt 改一句、Tool 加一个参数，行为漂移没人发现。spec-coding 把"对不对"变成可验证的闭环——先定契约（接口 / 数据 / 行为 / 边界 / 验收），编码对着契约写，验证独立跑，不靠"感觉差不多了"。

## 怎么用

两个方向：

**正向（spec → 代码，默认）**：

1. 把你的 Agent 需求写成 spec（需求 / 设计文档）；
2. skill 提取开发单元契约：接口、数据、行为、边界、验收标准；
3. 按契约编码；
4. strict-validation 独立校验「代码 vs 契约」直至 PASS。

**逆向（代码 → 文档，同步）**：Agent 上线演进后，skill 扫描真实代码回写文档，消除文档漂移。

```text
spec（需求/设计）
   ↓ 提取契约
契约（接口/数据/行为/边界/验收）
   ↓ 按契约编码
代码
   ↓ strict-validation 独立校验
PASS（代码忠实实现契约 + 文档与代码事实一致）
```

用 Claude Code 时该 skill 自动发现；其他 AI 工具按其 skill 机制加载 `.claude/skills/spec-coding/`。详细流程见 skill 内 `SKILL.md`。

## 边界

- spec-coding 是**开发期方法论** skill，不是 Agent 运行期能力；
- 它随开源核心附带，不强制使用——不用它系统照常跑；
- 编码中发现契约缺口允许自主决策技术性细节并事后补记，方向性选择才询问决策人。
