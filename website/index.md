---
layout: home

hero:
  name: "LubanAgent"
  text: "Agent 开发，从完整工程开始"
  tagline: 面向开发者的开源 Agent 工程基座。基于 Python、LangChain 和 LangGraph，把模型接入、工具调用、多 Agent、监控和部署放进一个可直接改造的源码仓库。你写业务，不必从头拼工程。
  actions:
    - theme: brand
      text: 了解 LubanAgent
      link: /manual/introduction
    - theme: alt
      text: 快速开始
      link: /manual/getting-started
    - theme: alt
      text: 查看源码
      link: https://github.com/lubanagent-dev/LuBan

features:
  - icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m8 8-4 4 4 4m8-8 4 4-4 4m-3-11-2 22"/></svg>'
    title: 你的业务，写在你的源码里
    details: 克隆后就是你的项目。业务代码放在 app/，框架代码放在 lubanagent/；按升级指引合并上游，减少定制代码与框架更新的冲突。
    link: /manual/customization/overview
    linkText: 查看改造方式
  - icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg>'
    title: 配置可改，运行有版本可查
    details: Agent 定义存入数据库，每次修改保留版本。调整人设和工具配置不必重新发版；运行记录绑定当时的定义版本。
    link: /manual/features/agent-definition
    linkText: 查看定义与版本
  - icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 1v5m6-5v5M9 18v5m6-5v5M1 9h5m-5 6h5m12-6h5m-5 6h5"/><rect x="10" y="10" width="4" height="4" rx="1"/></svg>'
    title: 换模型，不重写业务
    details: Agent 绑定 fast / smart / cheap / vision 逻辑槽位。通过模型中心调整映射，接入 OpenAI 兼容的云模型或本地模型。
    link: /manual/features/models
    linkText: 查看模型接入
  - icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/></svg>'
    title: 单 Agent、流程、团队一起运行
    details: 普通 Agent 不必手写图；固定步骤用 LangGraph 实现；复杂任务可由 supervisor 委派给子 Agent。三种执行方式共用运行记录与治理。
    link: /manual/features/graphs
    linkText: 查看流程与协作
  - icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 2 8 3v6c0 5-5 9-8 11-3-2-8-6-8-11V5l8-3Z"/><path d="M7 12h3l2-4 2 8 2-4h2"/></svg>'
    title: 自带监控，运行有边界
    details: 步数、预算、超时和取消由代码控制。Trace 记录模型与工具调用；观测可接 Langfuse 或 LangSmith，内部数据库始终保留运行事实。
    link: /manual/features/observability
    linkText: 查看运行观测
  - icon: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4M6 7h5m-5 4h8m3-4h1"/></svg>'
    title: 工作台与部署一起交付
    details: 自带定义编辑、模型管理、调试、Trace 和评测页面。开发期用 SQLite 起步，生产可使用 MySQL，通过 Docker 和 REST / SSE 接入业务。
    link: /manual/features/workbench
    linkText: 查看工作台
---

## LubanAgent 是什么

LubanAgent 是一套可以直接拿来开发业务 Agent 的完整工程，基于 Python ≥3.11、LangChain 和 LangGraph，使用 Apache-2.0 协议开源。

它交付的是源码仓库，不是托管平台。你可以在其中开发客服助手、内部知识助手、审批流程或多 Agent 系统，也可以通过 REST / SSE 把它作为独立服务接入现有业务。仓库自带智能客服示例，用来展示完整链路；客服只是示例，不是产品的使用边界。

如果熟悉 Java 生态，可以把它理解成：**若依把 Spring Boot 组装成业务项目的起点，LubanAgent 把 LangChain / LangGraph 组装成 Agent 项目的起点。** 这是交付方式的类比，不代表功能相同。

## 为什么不只写一个 Agent 脚本

模型能回答问题之后，工程工作才刚开始：定义怎样修改与回溯、模型怎样替换、工具权限怎样控制、失败现场怎样排查、服务怎样部署、业务代码怎样跟随框架升级。

LubanAgent 把这些环节放在同一个工程里。你仍然需要实现自己的业务工具、权限规则和验收标准，但不用先搭一套定义管理、模型工厂、运行记录和工作台，才能开始写业务。

[阅读项目介绍：适用场景、设计取舍，以及与 Agno、Dify、LangGraph 等项目的区别 →](/manual/introduction)

## 从这里开始

| 你现在要做什么 | 文档入口 |
| --- | --- |
| 判断这个项目是否适合我的团队 | [项目介绍与选型](/manual/introduction) |
| 先在本机跑通一次调用 | [快速开始](/manual/getting-started) |
| 接模型、写工具、加入自己的业务 | [开发指南](/tutorial/) |
| 查配置字段、API 或功能边界 | [使用手册](/manual/) |
| 理解源码结构和扩展位置 | [项目架构](/manual/architecture) |
| 准备部署、运维和升级 | [部署清单](/manual/deploy) · [升级指引](/manual/customization/upgrade) |
