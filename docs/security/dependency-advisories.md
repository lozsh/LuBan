# 依赖安全公告（H14 依赖扫描记录）

> 规则：优先兼容升级消除；无法消除的命中必须登记**公告、不可达/低危证据、理由、到期复核日**，
> 不允许全局忽略。本文件是 CI 安全扫描门槛的唯一豁免依据，每次复核后更新。

扫描日期：2026-10-15（npm audit / pip-audit，按 `package-lock.json` 与 `uv.lock` 锁定版本）。

## Python（pip-audit：已清零）

首轮命中 `cryptography`、`langgraph-sdk`、`pyjwt`、`pypdf`、`urllib3`（及 venv 自带 `pip`，非项目依赖），
全部在兼容范围内升级消除并刷新锁：`pypdf>=6.19`（直接依赖下限提升），其余四个传递依赖经
`uv lock --upgrade-package` 拉到已修复版。**当前 pip-audit 命中 0**；`mcp` 收敛为 `>=1.9,<2`
（2.x 改名 `streamablehttp_client`，未经迁移验证不升级，非安全命中）。

## 前端（npm audit：兼容范围内已修，余项如下）

`npm audit fix` 后 21 → 15（2 critical 均为 dev 工具链），生产依赖无 critical。

| 包 | 级别 | 现状 | 暴露面 | 理由与计划 | 复核日 |
|---|---|---|---|---|---|
| react-router-dom / react-router | moderate | 修复需升级 v7（跨主版本） | **运行时**（真实命中） | 开放重定向类公告（`<Link>`/`useNavigate` 反斜杠绕过）；工作台为单源内相对路径路由，外链跳转面极小。升级 v7 属 breaking（API 变更 + 全页面回归），列入下个版本专项 | 2026-12-31 |
| tailwindcss v3 链（braces / chokidar / fast-glob / micromatch / postcss-selector-parser / postcss-nested / tailwindcss-animate） | high/moderate | 无 v3 兼容修复（修复在 v4） | **仅构建期**：Tailwind/PostCSS 在构建时处理 CSS，产物只含生成的静态 CSS，这些包不进浏览器 bundle | 构建机本地 DoS 类公告（攻击者需已能改构建输入）。修复路径 = tailwind v4 主版本迁移，另立专项验证 | 2026-12-31 |
| vitest / tinypool / @vitest/mocker | critical/moderate | 修复需升级 vitest v5（跨主版本） | **仅测试期**：不进产物，不随镜像交付 | worker 原型污染/路径遍历需运行恶意测试代码才可触发。v5 升级与测试基建改造绑定，另立专项 | 2026-12-31 |
| vite v5 链（vite / esbuild） | high/moderate | 修复需升级 vite v6+（跨主版本） | **仅 dev server**：`npm run build` 产物不受 dev-server 公告影响；镜像内是构建产物而非 dev server | esbuild 请求泄露与 vite 路径遍历均需本地 dev server 才可利用。升级与 H14 后另立验证 | 2026-12-31 |

CI 门槛（`.github/workflows/ci.yml`）：`npm audit --omit=dev --audit-level=critical`（生产依赖出现
critical 即失败）+ 全量报告 artifact 存档；pip-audit 命中任何公告即失败。
