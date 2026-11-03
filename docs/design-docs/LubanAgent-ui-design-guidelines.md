# LubanAgent UI 设计准则

> 本文档是工作台前端（`lubanagent/ui/`）的样式与设计依据。口径综合自公开的主流设计标准：GOV.UK Design System（type scale，2022 可读性迭代版）、Ant Design、Element Plus、Microsoft Fluent 2、Material 3、GitHub Primer、WCAG 2.1。改样式先改这里，再改代码；两者不一致时以本文为准并回改代码。

## 1. 定位

工作台是**管理平台**（配置 Agent / 看 Run / 审批 / 评测），不是密集文字工具，也不是营销页。设计基调：**清晰、克制、层级分明**——信息密度中等偏上，但任何正文都不允许低于可读下限。

## 2. 字号阶梯（核心）

依据：GOV.UK 正文 19px / 标题 24-36-48px；Ant/Element/Fluent 中后台主力 14px；Primer UI 14-16px；博客正文普遍 17px+；Apple HIG 绝对下限 13pt；WCAG 最小可读 12px。管理平台取"比密集工具大一档"：

| Token | 字号 | 行高 | 用途 |
|---|---|---|---|
| `text-xs` | 13px | 20px | 细注：徽标、时间戳、表头单元。**禁止当正文用** |
| `text-sm` | 15px | 20px | 表格单元格、表单、列表项、按钮 |
| `text-base` | 17px | 25px | 正文、描述、聊天输出 |
| `text-lg` | 19px | 25px | 卡片标题、区块标题 |
| `text-xl` | 22px | 30px | 页面标题 |
| `text-2xl` | 24px | 30px | 仪表盘大标题 |
| `text-3xl+` | Tailwind 默认（36/40 起） | — | 数据大屏数字，慎用 |

硬规则：
- **行高取 5px 的倍数**（GOV.UK 垂直节奏），保证列表/表格可扫读；
- 正文字号下限 15px，细注下限 13px——12px 及以下仅允许出现在角标（badge 圆点），不允许承载文字信息；
- 字阶调整只动 `tailwind.config.js` 的 `fontSize`，禁止在组件里写 `text-[Npx]` 例外值（存量已清零，lint 可拦回归）。

层级语义（钉死，不许降档）：
- 页面标题 `h1` = `text-xl`；仪表盘大标题/流程图等核心页可用 `text-2xl`；
- 区块/卡片标题 `h2`、侧边栏导航项 = `text-base`；品牌名 = `text-lg`；
- 聊天流内三级标题 `h3` = `text-base`，`h4` 及以下 = `text-sm`；
- 表格单元格、表单、按钮 = `text-sm`；徽标/时间戳 = `text-xs`。

## 3. 字体

系统栈优先（离线优先产品，不引外部字体）：

```css
font-family: system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei',
  'Segoe UI', sans-serif;
```

中文优先声明 PingFang/雅黑，保证 Windows 下中文渲染不虚。等宽（Run id / JSON / 日志）用 `font-mono`（Tailwind 默认 mono 栈）。

## 4. 色彩与对比

- 全部语义色走 CSS 变量（`--primary` 等，shadcn 体系），组件不得硬编码色值；
- 正文对背景对比度 ≥ **WCAG AA（4.5:1）**，大字（≥19px bold）≥ 3:1；
- 状态色语义固定：destructive=失败/危险、amber=等待审批、emerald=成功、blue=进行中，跨页面一致，不自造状态色。

## 5. 间距与布局

- 间距用 4px 基数（Tailwind 默认），卡片内边距 `p-4` 起步，页面留白 `p-6`；
- 相关内容靠近、无关内容拉开——层级靠间距与字阶表达，不靠加分割线；
- 密集表格可用紧凑模式（`text-sm` + `py-2`），但正文区一律 `text-base`。

## 6. 组件与交互

- 状态反馈三件套不可少：loading（spinner/skeleton）、空态（一句说明 + 下一步动作）、错误（human-readable 原因，参考后端 ErrorOut 语义）；
- 破坏性操作必须二次确认；审批类操作结果即时可见（乐观更新或事件回流）；
- 动效克制：150-200ms 过渡，仅用于状态变化（hover、弹层、列表载入），不做装饰性动画。

## 7. 落地约定

- 字号/行高唯一来源：`lubanagent/ui/tailwind.config.js` → `theme.extend.fontSize`；
- 本准则的变更随 CHANGELOG 记 `[framework]`（UI 属框架领地）；
- 新页面上线前对照第 2、4 节自查：正文 ≥15px？细注 ≤13px 且不承载关键信息？对比度达标？
