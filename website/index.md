---
layout: home

hero:
  name: "LubanAgent"
  text: "开源 Agent 工程基座"
  tagline: Python ≥3.11 + LangChain + LangGraph。克隆仓库、配一个模型，第一天就能写自己的业务 Agent——定义入库版本化、模型工厂、治理与监控、开发者工作台，开箱即用。
  actions:
    - theme: brand
      text: 快速开始 · 30 分钟跑起来
      link: /manual/getting-started
    - theme: alt
      text: 新手教程（8 课从零到部署）
      link: /tutorial/
    - theme: alt
      text: 使用手册
      link: /manual/
    - theme: alt
      text: GitHub ↗
      link: https://github.com/lozsh/LuBan

features:
  - icon: 📦
    title: Agent 定义入库版本化
    details: 人设 / 工具 / 规矩是一份存库、版本化的定义——改配置不发版，运行期生效。
    link: /manual/features/agent-definition
  - icon: 🧠
    title: 模型工厂 · 四槽位
    details: fast / smart / cheap / vision 逻辑槽位解耦 Agent 与模型，换模型 = 改库映射，不改代码不发版。
    link: /manual/features/models
  - icon: 🛡️
    title: 治理与观测内置
    details: 步数 / 预算 / 超时 / 取消熔断，费用归因，Run / Trace 全程留痕可回放。
    link: /manual/features/observability
  - icon: ⚙️
    title: 一个引擎两种用法
    details: 日常 Agent 不写图；确定性流程手写 LangGraph；多 Agent supervisor 委派——三者同库同治理。
    link: /manual/features/graphs
  - icon: 🖥️
    title: 开发者工作台
    details: Agents / Prompt / 模型 / 工具 / 知识库 / Traces / 评测等 9 个管理页面 + 用户端聊天，开箱即用。
    link: /manual/features/workbench
  - icon: ⬆️
    title: 跟上游升级不丢改动
    details: 框架 / 用户领地隔离 + 升级契约，克隆后长期住进去迭代，不怕合并冲突。
    link: /manual/customization/upgrade
---

## 快速导航

| 我想…… | 去哪 |
| --- | --- |
| 搞懂 Agent 是什么（零基础） | [教程第 0 课](/tutorial/00-primer) |
| 30 分钟跑起来看效果 | [快速开始](/manual/getting-started) |
| 写第一个自己的 Agent | [教程第 2 课](/tutorial/02-first-agent) |
| 查某个 API 怎么调 | [API 参考](/manual/api/rest) |
| 把基座改造成我的业务系统 | [改造指南](/manual/customization/overview) |
| 看懂项目架构和领地规则 | [项目架构](/manual/architecture) |
| 部署到生产环境 | [部署与生产清单](/manual/deploy) |
| 查一个术语 | [术语表](/manual/glossary) |

## 文档之间的分工

- **教程**（`website/tutorial/`）：线性、叙事、动手。8 课从装环境到部署上生产，每课带你做出一点东西，学完你会拥有一个可以写进简历的完整 Agent 项目。
- **使用手册**（`website/manual/`）：字典式、按需查。每个功能一页（是什么 / 为什么 / 怎么用 / 边界），全量字段、全量 API、全量改造场景。
- 两套文档共用同一套术语与同一套示例（图书馆借阅助手 + 内置客服），教程每课末尾的"延伸阅读"指向手册对应页。
- 仓库 `docs/` 目录是**开发用文档**（设计文档 / 模块规格 / 契约），面向贡献者与 AI 协作工具，不在本站内。
