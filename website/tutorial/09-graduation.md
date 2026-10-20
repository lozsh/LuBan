# 结课：毕业设计与验收

> 学完本课你能：用一份自己写的验收单证明这 8 课的成果，选定并完成一个毕业设计方向 ｜ 预计耗时 2-6 小时（取决于方向） ｜ 前置：第 8 课 ｜ 难度 ★★★

课程到这里就结束了。最后一课没有新知识，只有一件事：**把"学完了"变成"能证明"**。

## 1. 你现在拥有什么

走到这里，你拥有的不是一个"跟练 demo"，是一个可以写进简历、可以拿出来演示的完整项目：

- **一个你自己命名的 Agent 产品**（my-lib-assistant 或你的场景）：人设、真实模型、自写的低/高风险工具（含审批流）、校准过阈值的知识库、8 用例评测集与基线、一条含人工节点的图流程、一个路由正确的团队——全部住在你的 `app/` 领地；
- **可量化的证据**：评测报告、Trace 与费用归因；
- **可运行的部署**：五服务 Docker 栈 + Langfuse 观测；
- **一次真实演练过的升级路径**：领地契约不是纸面承诺。

## 2. 毕业验收单

第 8 课练习 C 你已经写了自己的「毕业验收单」：8-10 条验收项（至少覆盖三种执行形态各一条、守门一条、评测回归一条、升级合并一条），逐条跑通并留证据。这份单子就是你以后带自己的 Agent 项目上线的 checklist 初稿——**留着它，比课程本身值钱**。

## 3. 毕业设计建议（六选一）

| 方向 | 难度 | 依赖课程 | 验收标准 |
|---|---|---|---|
| 课程答疑助手（RAG 重） | ★★ | 2-5 | 30+ 条评测集全绿，含 5 条对抗性拒答用例 |
| 审批型办事助手（流程重） | ★★★ | 4、7 | 高风险操作 100% 走审批；图含 human 节点且 interrupt/resume 可演示 |
| 技术支持分流台（多 Agent 重） | ★★★ | 7 | supervisor 路由准确率有评测数字；max_delegations 守住 |
| 评测驱动的 Prompt 迭代报告（方法重） | ★★ | 6 | ≥3 轮「改-评-对比」记录，`--compare` 出回归结论 |
| SOP Skill 化助手（知识工程重） | ★★ | 4、5 | 自建 SKILL.md + scripts，zip 上传可用（参考[Skill 能力包](../manual/features/skills.md)） |
| 全栈部署运维报告（部署重） | ★★ | 8 | 五服务部署 + 成本分析 + 一次升级合并实录 |

每个方向都请附一段**2 分钟演示脚本**（讲什么、演示什么、晒哪张截图）——学生晒作品，就是这个项目最好的传播。

## 4. 结课之后去哪

| 你想 | 去哪 |
|---|---|
| 查某个功能的全部字段与边界 | [使用手册·功能手册](../manual/index.md) |
| 接自己的业务系统（REST/SSE/审批回调） | [接入业务系统](../manual/customization/business-integration.md) |
| 换模型、换观测后端、换数据库 | [换模型与换后端](../manual/customization/swap-backends.md) |
| 跟上游版本升级 | [跟上游升级](../manual/customization/upgrade.md) |
| 把开发过程也工程化 | [用 spec-coding 开发](../manual/customization/spec-coding.md) |

## 5. 最后一句

欢迎把你的毕业作品、Trace 截图、验收单发到社区（Issue/讨论区）。遇到坑也欢迎提——教程的"常见坑"一节就是这样长出来的。

---

延伸阅读：[docs/UPGRADE.md](https://github.com/lozsh/LuBan/blob/master/docs/UPGRADE.md)（升级合并指引）、[SECURITY.md](https://github.com/lozsh/LuBan/blob/master/SECURITY.md)（部署安全须知）｜ 涉及文件：`docker-compose.yml`、`docker/Dockerfile`、`.lubanagent-upstream`、`CHANGELOG.md`
