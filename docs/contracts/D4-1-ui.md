# 契约 D4-1：`lubanagent/ui` 开发者工作台（前端）

> 来源：开发计划 D4-1、技术方案 §5.7、用户手册 §11、架构设计 §7.10（React 18 + Vite + TS + Tailwind + shadcn/ui）。
> 前置：D1-3（REST/SSE API、`/openapi.json`）。Node v22 + npm 可用。
> 执行拆分：G0 脚手架（含后端小补丁）→ G1/G2/G3 三组页面并行 → 集成验收。

## 契约决策

1. **目录**：`lubanagent/ui/`（独立前端工程，`.gitignore` 已排除其 `dist/` 与 `node_modules/`）。包管理用 **npm**（lock 文件入仓：package-lock.json）。
2. **栈（架构 §7.10 钉死，不换）**：React 18 + Vite 5 + TypeScript(strict) + Tailwind CSS 3 + react-router-dom 6 + @tanstack/react-query 5。shadcn/ui 组件**源码复制**进 `src/components/ui/`（非 npm 黑盒，用户可改——本单元引入 button/card/input/textarea/select/tabs/table/badge/dialog/scroll-area/separator/label/switch/tooltip 必要子集，class-variance-authority/clsx/tailwind-merge/radix 依赖按 shadcn 官方清单）。
3. **契约先行**：`npm run codegen` = `openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts` + openapi-fetch client 封装 `src/api/client.ts`；**codegen 产物入仓**（验收原文）；CI 用本地导出的 openapi.json 文件重生成做一致性 diff（`npm run codegen:check`，输入 `../openapi.snapshot.json`——由后端测试导出，G0 负责在后端测试落地 `tests/lubanagent/api/test_openapi_snapshot.py` 写盘快照）。
4. **Mock**：MSW 2（`src/mocks/`，handlers 覆盖全部已接路由 + fixtures），`npm run dev` 默认开 mock（`VITE_USE_MOCK=true`），`VITE_API_BASE` 切真后端。**全部页面在 mock 下可点可用**（验收原文）。
5. **SSE**：自实现 `useRunStream` hook（fetch + ReadableStream 逐行解析 `event:`/`data:`，等价实现 Vercel AI SDK 流式渲染语义；不引 AI SDK）。
6. **状态**：服务端状态一律 react-query；跨页共享（当前选中 Agent）用轻量 context；不引 redux。
7. **后端小补丁（G0 范围，授权）**：
   - `api/routes/tools.py`：`GET /v1/tools`（Registry 列出 ToolDescriptor）、`POST /v1/tools/{key}/test`（body=args dict，经 `debug_tool` 执行返回 ToolResult）——Tool 在线测试的真后端；
   - `app/factory.py`：`lubanagent/ui/dist` 存在时 `app.mount("/", StaticFiles(directory=dist, html=True))`（生产单进程托管，路由 fallback 到 index.html——用 `StaticFiles(html=True)` + 自定义 404 回退或 `app.exception_handler(404)` 对非 /v1 路径返 index.html）；
   - `tests/lubanagent/api/test_openapi_snapshot.py`：导出 openapi.snapshot.json 到仓库根（供 codegen:check）。
8. **不做**：登录/多租户/可视化编排编辑器（图只读展示）、数据大盘（成本报表 API 未开放，D4-1 不做成本页）。

## 页面清单（路由 = 验收单位）

| 路由 | 页面 | 核心能力 | API |
|---|---|---|---|
| `/agents` | Agent 列表 | 卡片/表格、状态徽章、新建入口 | GET /v1/agents |
| `/agents/:key` | Agent 定义编辑器 | 分区表单（身份/指令/大脑/工具/边界/IO）、保存=新建版本、版本列表与切换查看 | GET/POST /v1/agents、POST /v1/agents/{key}/versions、GET /v1/agents/{key}/versions/{v} |
| `/agents/:key/debug` | 运行调试台 | 输入框 + SSE 流渲染（token 打字机、tool.started/finished 条目、run.completed 汇总：模型/用量/结束原因）、取消按钮 | POST /v1/agents/{key}/runs（SSE）、POST /v1/runs/{id}/cancel |
| `/playground` | Prompt 调试 | 选 Agent、改 instructions/variables（不入库）、试跑对比原定义结果 | 同上（variables 字段） |
| `/models` | 模型中心 | 模型表（脱敏）、大脑映射编辑（四槽位下拉）、保存映射 | GET /v1/models、PUT /v1/models、PUT /v1/brain-mappings |
| `/models/compare` | 模型对比 | 同一输入两次试跑（两个 brain/profile 或两次 run）并排：输出/耗时/token | POST runs ×2 |
| `/tools` | Tool 中心 | 列表（风险徽章/超时/幂等）、详情（args_schema 渲染表单）、在线执行看结果 | GET /v1/tools、POST /v1/tools/{key}/test |
| `/skills` | Skill 管理 | 列表 + manifest 查看（上传/分配标记"即将支持"占位，不假装可用） | GET /v1/skills |
| `/traces` | Trace 列表 | Run 表（状态/模型/用量/耗时/结束原因），可筛选 agent | GET /v1/agents（辅助）+ Run 数据（经 /v1/traces/{run_id} 逐条或列表——若无列表端点，页面以"输入 run_id 查看 + 最近 runs"形态：G0 决策补 `GET /v1/runs` 列表端点（query: agent_key/session_id/limit），授权） |
| `/traces/:runId` | Trace 回放 | 事件时间线（类型着色/时间轴/步骤分组）、事件详情 JSON、七问摘要卡 | GET /v1/traces/{run_id} |
| `/evals` | 评测报告 | 数据集报告列表/详情（pass_rate、逐 case verdicts）——API 为 stub，页面先吃 mock，注明真源待 API 填实 | GET /v1/evals（stub） |

布局：左侧导航（Agents/调试台/Prompt/模型/工具/Skills/Traces/评测），顶栏（当前环境徽章、API 连通状态）。风格：开发者工具审美（密度高、键盘友好、暗色优先——class 切换，默认 dark）。

## 工程约定

- `src/` 结构：`api/`（schema.d.ts、client.ts、hooks.ts）`components/ui/`（shadcn 复制）`components/`（业务组件）`pages/<name>/` `routes.tsx` `lib/`（sse.ts、utils.ts、format.ts）`mocks/`（handlers、fixtures、browser.ts）`styles.css`。
- 路径别名 `@/` → `src/`；ESLint(flat config, typescript-eslint) + Prettier；`npm run lint`/`typecheck` 入验收。
- 测试：vitest + @testing-library/react + jsdom——关键组件与 useRunStream（MSW node 模式模拟 SSE 流）有测试；`npm run test` 全绿。
- 构建：`npm run build` 通过（tsc + vite build），产物 `dist/`。

## 验收标准（须实跑）

1. `npm ci && npm run dev`（mock 模式）可起，**全部 11 个页面路由可点可用**（Playwright 冒烟：逐路由打开无白屏无 console error，关键交互各一次：Agent 保存版本、debug 跑一次 SSE（mock SSE 流）、模型对比并排渲染、Tool 在线执行、Trace 回放时间线渲染）。
2. `npm run codegen:check`：从 openapi.snapshot.json 重新生成与入仓产物一致（diff 为空）。
3. `npm run typecheck && npm run lint && npm run test && npm run build` 全绿。
4. 接真后端（FastAPI + seed + fake 模型）：改 Agent 定义生成新版本 → debug 页触发 Run 看到 SSE 流 → Trace 页回放该 Run → 模型对比页并排两次试跑（集成冒烟可脚本化：起 uvicorn + curl 验证后端补丁端点 + 静态托管）。
5. shadcn 组件为源码复制（`src/components/ui/` 无 npm 依赖黑盒证据：package.json 无 shadcn 包）。

## 边界

- 工作台是开发辅助，不做托管/多租户（技术方案 §5.7）。
- 前端不复制后端业务规则（校验/脱敏以后端为准，表单校验只做 UX 层）。
- mock fixtures 的数据形态必须来自 schema.d.ts（对着契约写 mock，不编造字段）。

## 存疑项与决策

1. SSE 不引 Vercel AI SDK（自实现等价 hook，决策 5——减少黑盒依赖，格式是我们自己的协议）。
2. Skill 上传/分配、评测触发、成本页 → 显式占位或省略（对应 API 未开放，不造假功能）。
3. `GET /v1/runs` 列表端点为 G0 授权补充（Trace 列表页必需，契约遗漏的合理补齐）。
