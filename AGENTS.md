# LubanAgent 协作约定

> 本文件是本仓库所有 AI 协作工具（Claude Code、Codex、Gemini CLI、Copilot 等）共同遵守的项目约定。
> 产品定义见 `docs/product-specs/LubanAgent-prd.md`，技术方案见 `docs/design-docs/LubanAgent-technical-proposal.md`，架构设计见 `docs/design-docs/LubanAgent-architecture-design.md`，功能定义见 `website/manual/`（使用手册，随文档站维护），开发计划见 `docs/design-docs/LubanAgent-development-plan.md`。本文只钉死"不可动摇的硬约束"与"怎么和我协作"。

## 1. 项目是什么

LubanAgent 是开源（Apache-2.0）的 ** Agent 起步基座**：Python ≥3.11 + LangChain + LangGraph。用户克隆仓库后长期住进去迭代、上生产，不是搭完就扔的实验原型。
系统内置智能客服作为首个完整验证场景。

## 2. 不可动摇的产品定位（动文档前先对照）

1. **主线 = Agent系统起步基座 + 自带监控**。治理 / 评测 / 成本是"基座成色"，不是主战场——别把它们写成差异化护城河，也别本末倒置。
2. **代码优先、全源码、无黑盒、无强制 SaaS 依赖**。确定性规则（权限 / 金额 / 审批）由代码执行，不靠 Prompt 声明。
3. **多 Agent 编排是一等卖点**，与Agent基座不冲突——LangGraph 给引擎，LubanAgent 组装完整工程基座；编排 Run 与单 Agent Run 同库同治理同部署。不要把多 Agent 往"后期扩展 / 可选附属"上靠。
4. **不做**：在线托管 SaaS、低代码可视化设计器、模型/Prompt/插件商城、通用后台管理（用户/角色/权限）、自建观测/评测平台、具体业务项目（客服是示例非产品）。
5. **与主流开源 Agent 框架的关系**：LangGraph/CrewAI/AutoGen/MetaGPT/Dify/n8n/LlamaIndex/OpenAI Agents SDK 分属"底层编排引擎/多 Agent 协作框架/低代码平台/领域专用库"四个不同赛道，LubanAgent 把 LangGraph 当依赖，补的是这些框架普遍缺失的一层：Agent 定义入库版本化、模型工厂、治理层、部署基座。不是再造一个编排框架去抢赛道，别把 LubanAgent 定位混淆成"又一个 CrewAI/Dify"。详细选型对比见 `docs/product-specs/LubanAgent-prd.md` §7。

## 3. 技术栈与范围

- **语言/框架**：Python ≥3.11 + LangChain + LangGraph。这是硬约束，不换。
- **模型接入**：OpenAI 兼容协议优先（DeepSeek/Qwen/GLM/vLLM/Ollama 一个客户端全覆盖），厂商原生 SDK 按需补。
- **数据库**：MySQL 8.0+，开发期 SQLite 免安装。
- **范围政策**：**AI 写代码，不考虑人工投入——功能不分期，一次性交付完整能力**。不要用 P0/P1/P2 分期框架。只保留"构建依赖顺序"（核心领地→能力层治理层→上层编排多 Agent），这是工程依赖序，不是产品分期。运行期可选能力（RAG/记忆/图编排/多 Agent）默认不启用但都是一等能力。
- **交付形态（重要商业边界）**：**开源核心（Agent 本体，F1-F13，Apache-2.0）+ 商用渠道层（F14-F16，独立可拆卸模块，开源版不含、商用版提供）**。渠道层 = 消息渠道接入（微信/飞书/钉钉等 IM + 京东/淘宝/拼多多等电商客服）、会话状态机、出站风控。架构硬约束：**核心不依赖渠道层**（业务系统走 REST/SSE 调用即独立运行），渠道层单向依赖核心（`lubanagent-channels → lubanagent` 允许、反向禁止，import-linter 守卫）；核心只定义 `ChannelConnector`/`SessionStateStore`/`OutputGuard` 三个扩展点，渠道层实现可整体替换或卸载。渠道层不开源的理由：IM/电商渠道接口与规则频繁变更、维护成本高，不宜作开源社区资产。**用户手册只写开源核心部分，不写渠道层。**

## 4. 架构关键契约（改代码/写代码必须守住）

- **领地三重隔离**：`lubanagent/`（框架领地）与 `app/`（用户领地）之间——
  1. 文件系统隔离（用户领地独立目录树）；
  2. 依赖方向单向：`app/* → lubanagent/*` 允许；`lubanagent/* → app/*` **禁止**（框架运行期通过 Registry 找用户扩展）；
  3. `.lubanagent-upstream` 清单 + import-linter CI 守卫：上游不主动触碰用户领地（CI 拦截 user-owned 命中），显著降低合并冲突；不承诺任意 git merge 零冲突。
- **升级契约**：用户克隆改造后能跟上游版本。CHANGELOG 按领地分类（`[framework]`/`[scaffold-seed]`/`[shared]`/`[migration]`/`[breaking]`）+ UPGRADE.md 合并指令。
- **Agent 定义层**：定义本体是数据库记录（运行期可改、库内版本化），YAML 仅用于初始化导入 / 导出迁移，不是存储。
- **逻辑大脑**：fast/smart/cheap/vision 四槽位解耦 Agent 与具体模型；切换模型 = 改库映射，不改代码不发版。
- **一个引擎两种用法**：执行层统一跑在 LangGraph 上——普通 Agent = prebuilt agent + 治理包装（步数/预算/超时/取消/Trace，用户不写图）；确定性流程 = 手写 LangGraph 图；多 Agent 是 LangGraph 之上的 supervisor/子 Agent 委派拓扑。三者共享同一份能力件与治理，编排 Run 是会话 Run 的子 Run（预算继承、取消传播，见技术方案 §10.6）。
- **监控采用 C 方案**：可插拔 `ObservabilityBackend` Protocol，默认 Langfuse 自部署（保数据主权），可选 LangSmith，可切 none；内部 DB 始终是 source of truth（双写）。不宣传"完全等价切换"。

## 5. 文档规范

- **统一改，不要问**：文档问题直接改全仓库使其自洽，不要列 a/b/c 让用户拍板琐碎决策。
- **简洁、清晰、门槛低**——"好文档是产品最大的卖点"。用户文档对用户说话：
  - 不要研发内部口吻（"功能定义草案""评审依据"）；
  - 不要内部编号噪音（如痛点编号 P1-P7、分期 P0/P1/P2）；
  - 竞品对比属于 PRD，不塞进用户文档；
  - 每节统一"是什么 / 为什么 / 怎么用 / 边界"四段；示例只用 YAML / curl / Python 三种开发者熟悉的形态。
- **保持四份文档自洽**：改一处定位/范围/架构决策，必须同步 PRD / 技术方案 / 架构设计 / 用户文档。
- **代码改完必过文档影响闸门**（spec-coding §F6）：任何代码改动收尾（正向 PASS、hotfix、重构、bug 修复）不得直接结束，先判断是否触及接口/数据/行为/边界四类契约。命中→派 worker subagent 取证补文 + strict-validation subagent 审稿，别靠主线程记忆硬扛全仓；未命中→记一句理由跳过。判断由主 agent 做，补文执行派 subagent。

## 6. 怎么和我协作

- **不需要我拍板的事就直接做**。常规修改、文档对齐、清矛盾——直接执行并报告结果，不要用选择题问。
- **抓重点，别啰嗦**。总结要短，先结论后细节。
- **要拍板的才问**：涉及方向选择、新决策、不可逆操作时才问，且把选项和理由给清楚。
- **改完要验证**：用 grep 核查残留矛盾（如分期标签、痛点编号），别只改一处漏一片。

## 7. 开发方法论：spec-coding

本仓库内置 spec-coding skill（`.claude/skills/spec-coding/`，克隆即用）——规格驱动开发闭环：

- **正向（spec→code，默认）**：从 spec（PRD/技术方案/架构/开发计划）提取开发单元契约（接口/数据/行为/边界/验收），按契约编码，独立 strict-validation subagent 校验「代码 vs 契约」直至 PASS。
- **逆向（code→docs，同步）**：代码演进后扫描真实代码回写 `docs/`，校验「文档 vs 代码事实」。

这是「若依式起步基座」的方法论成色：基座不只给运行骨架，还给可复用的工程纪律。开发本仓库与用户开发自己 Agent 都走这套。skill 无内部依赖、语言无关，细节见 skill 内 `SKILL.md`。
