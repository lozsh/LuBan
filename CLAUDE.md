# CLAUDE.md

> 本文件是 Claude Code 在本仓库工作时的专属指令。**产品定位、技术栈、范围政策、架构契约、文档规范、协作方式等核心约定统一在 `AGENTS.md`，开始任何工作前先读它并遵守。**

@AGENTS.md

## Claude Code 专属补充

### 工作方式
- **能直接做就直接做，先做后报**。文档对齐、清矛盾、按既有约定补全——直接执行，别用 AskUserQuestion 问琐碎选择。只有方向选择、新决策、不可逆操作才问。
- **抓重点**：回复先给结论，细节按需展开。用户多次反馈"说太多看不到重点"。
- **改文档要全仓库自洽**：定位/范围/架构决策一改，PRD / 技术方案 / 架构设计 / 用户文档 / 开发计划五处同步，改完用 `grep` 核查残留（分期标签 P0/P1/P2、痛点编号、竞品段落等）。

### 工具偏好
- **搜索代码/文件**：优先用 Grep/Glob/Read 等专用工具，不用 `cat`/`grep` shell 凑。
- **多步任务**：用 TaskCreate 跟踪，但别为 1-2 步的琐事建任务列表。
- **写代码前的设计**：非平凡实现任务先 EnterPlanMode 探代码库、出方案，别上来就写。
- **规格驱动编码**：实现开发计划单元时用项目内置 `.claude/skills/spec-coding/` skill（正向 spec→契约→code→strict-validation 闭环）；代码演进后更新文档用其逆向模式。
- **代码改完必过文档影响闸门**（spec-coding §F6）：任何代码改动收尾——正向 PASS、hotfix、重构、bug 修复——不得直接结束，先判断是否触及接口/数据/行为/边界四类契约。命中则派 worker subagent 取证补文 + strict-validation subagent 审稿，别靠主线程记忆硬扛全仓；未命中记一句理由跳过。判断由主 agent 做，补文执行派 subagent。

### 当前项目状态（2026-07）
- 五份文档（PRD / 技术方案 / 架构设计 / 用户文档 / 开发计划）已定稿并自洽。定位 = 若依式起步基座 + 自带监控 + 多 Agent 编排一等卖点；功能不分期，AI 一次性交付。
- **交付形态 = 开源核心（F1-F13，Apache-2.0）+ 商用渠道层（F14-F16，独立可拆卸模块，开源版不含）**。核心不依赖渠道层、留 `ChannelConnector` 扩展点；渠道层单向依赖核心。用户手册只写开源核心。
- 真实需求印证：智能客服落地实践中，Agent 本体仅占约 1/6 工作量，渠道接入/会话状态/出站风控占 5/6 → 商品化为渠道层。
- **进度**：开发计划（`docs/design-docs/LubanAgent-development-plan.md`）执行中——阶段 0-5 开源核心功能全部完成（D0 地基 / D1 核心基座 / D2 评测成本 / D3 编排扩展 / D4-1 前端工作台 / D5-1 客服示例纵切，586 后端测试 + 70 前端测试全绿，逐批 strict-validation PASS，批次 A-H 已提交）；收尾批次 I（D1-9：CI 守门 + 覆盖率门槛 + .lubanagent-upstream 守卫 + Docker 交付）进行中；阶段 6 渠道层（商用，不在本仓库）未开始。开发遵循内置 spec-coding skill（规格驱动闭环，见 `.claude/skills/spec-coding/`，是本仓库方法论卖点）。
- **新手教程（2026-09）**：`website/tutorial/` 8+1 课线性动手教程（图书馆借阅助手主线），与使用手册（`website/manual/`，随 VitePress 文档站独立部署）构成「学/查」互补，主 README 双语入口。教程事实资产登记在 `docs/specs/modules/tutorial.md`（漂移抽查联动）。零密钥口径 = 只验链路（FakeLLM 固定台词），完整体验需真模型——demo README 同口径。教程写作揪出并修复两个首发即坏 bug：R12（`luban eval run` 独立进程缺装配三件套）与 R13（context_builder format_map 炸 supervisor JSON 指令、团队 Run 全挂），详见 risks.md。
