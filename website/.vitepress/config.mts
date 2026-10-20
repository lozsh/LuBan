import { defineConfig } from 'vitepress';
import { withMermaid } from 'vitepress-plugin-mermaid';

// GitHub Pages 项目页部署时用 DOCS_BASE=/<repo>/ npm run build 覆盖
const base = process.env.DOCS_BASE || '/';

// 侧栏配置：必须放在 themeConfig.sidebar（默认主题从 theme 读取 sidebar），
// 写在 defineConfig 顶层会被静默忽略，导致全站文章页没有左侧菜单
const sidebar = {
  // ------------------------------------------------ 教程（线性课程）
  '/tutorial/': [
    {
      text: '开篇',
      items: [{ text: '开篇词：8 课从零到部署', link: '/tutorial/' }],
    },
    {
      text: '入门篇',
      collapsed: false,
      items: [
        { text: '第 0 课 · 认识 Agent', link: '/tutorial/00-primer' },
        { text: '第 1 课 · 跑起来', link: '/tutorial/01-setup-and-first-run' },
        { text: '第 2 课 · 第一个自己的 Agent', link: '/tutorial/02-first-agent' },
      ],
    },
    {
      text: '核心篇',
      collapsed: false,
      items: [
        { text: '第 3 课 · 接真实模型', link: '/tutorial/03-real-models' },
        { text: '第 4 课 · 写自己的 Tool', link: '/tutorial/04-write-a-tool' },
        { text: '第 5 课 · 给 Agent 加知识', link: '/tutorial/05-knowledge-rag' },
        { text: '第 6 课 · 用评测保质量', link: '/tutorial/06-evals' },
      ],
    },
    {
      text: '进阶篇',
      collapsed: false,
      items: [{ text: '第 7 课 · 确定性流程与多 Agent', link: '/tutorial/07-graphs-and-teams' }],
    },
    {
      text: '上线篇',
      collapsed: false,
      items: [{ text: '第 8 课 · 部署上生产', link: '/tutorial/08-deploy' }],
    },
    {
      text: '结课',
      collapsed: false,
      items: [{ text: '结课 · 毕业设计与验收', link: '/tutorial/09-graduation' }],
    },
  ],
  // ------------------------------------------------ 使用手册（字典式）
  '/manual/': [
    {
      text: '开始',
      collapsed: false,
      items: [
        { text: '手册导航', link: '/manual/' },
        { text: '快速开始', link: '/manual/getting-started' },
        { text: '项目架构', link: '/manual/architecture' },
      ],
    },
    {
      text: '功能手册',
      collapsed: false,
      items: [
        { text: 'Agent 定义与版本化', link: '/manual/features/agent-definition' },
        { text: 'Prompt 库', link: '/manual/features/prompt-library' },
        { text: '模型中心与模型工厂', link: '/manual/features/models' },
        { text: 'Agent Runtime', link: '/manual/features/runtime' },
        { text: 'Tool 与 MCP', link: '/manual/features/tools-mcp' },
        { text: 'Skill 能力包', link: '/manual/features/skills' },
        { text: '知识与记忆', link: '/manual/features/knowledge-memory' },
        { text: '运行观测', link: '/manual/features/observability' },
        { text: '评测体系', link: '/manual/features/evals' },
        { text: '成本治理', link: '/manual/features/cost' },
        { text: '图编排', link: '/manual/features/graphs' },
        { text: '多 Agent 协作', link: '/manual/features/multiagent' },
        { text: '开发者工作台', link: '/manual/features/workbench' },
        { text: '内置示例：智能客服', link: '/manual/features/demo' },
      ],
    },
    {
      text: 'API 参考',
      collapsed: false,
      items: [
        { text: 'REST API', link: '/manual/api/rest' },
        { text: 'SSE 事件与错误码', link: '/manual/api/sse-and-errors' },
        { text: 'CLI 命令', link: '/manual/api/cli' },
      ],
    },
    {
      text: '改造指南',
      collapsed: false,
      items: [
        { text: '改造总览与领地规则', link: '/manual/customization/overview' },
        { text: '写 Tool 与 Skill', link: '/manual/customization/tools-skills' },
        { text: '写图编排与团队', link: '/manual/customization/graphs-teams' },
        { text: '换模型与换后端', link: '/manual/customization/swap-backends' },
        { text: '接入业务系统', link: '/manual/customization/business-integration' },
        { text: '技术栈选型与可替换件', link: '/manual/customization/tech-stack' },
        { text: '跟上游升级', link: '/manual/customization/upgrade' },
        { text: '用 spec-coding 开发', link: '/manual/customization/spec-coding' },
      ],
    },
    {
      text: '部署运维',
      collapsed: false,
      items: [
        { text: '部署与生产清单', link: '/manual/deploy' },
        { text: '术语表', link: '/manual/glossary' },
      ],
    },
  ],
};

export default withMermaid(
  defineConfig({
    lang: 'zh-CN',
    title: 'LubanAgent',
    description: '开源 Agent 工程基座：教程 + 使用手册',
    base,
    lastUpdated: true,
    ignoreDeadLinks: [
      // 本机服务地址（localhost:8000/5173/3000 等）是课文里的操作目标，不是站内链接
      /^https?:\/\/localhost/,
    ],
    head: [
      // 内联 SVG favicon，避免 favicon.ico 404
      ['link', { rel: 'icon', type: 'image/svg+xml', href: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%A7%B0%3C/text%3E%3C/svg%3E" }],
      // 社交分享元信息（对齐 Vite 官网做法）
      ['meta', { property: 'og:type', content: 'website' }],
      ['meta', { property: 'og:title', content: 'LubanAgent 文档' }],
      ['meta', { property: 'og:site_name', content: 'LubanAgent' }],
      ['meta', { property: 'og:description', content: '开源 Agent 工程基座：8 课新手教程 + 字典式使用手册' }],
      ['meta', { name: 'theme-color', content: '#5b5ef4' }],
    ],
    themeConfig: {
      sidebar,
      siteTitle: 'LubanAgent 文档',
      search: {
        provider: 'local',
        options: {
          translations: {
            button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
            modal: {
              noResultsText: '没有结果',
              resetButtonTitle: '清除查询',
              footer: { selectText: '选择', navigateText: '切换', closeText: '关闭' },
            },
          },
        },
      },
      socialLinks: [{ icon: 'github', link: 'https://github.com/lozsh/LuBan' }],
      outline: { level: [2, 3], label: '本页目录' },
      editLink: {
        pattern: 'https://github.com/lozsh/LuBan/edit/master/website/:path',
        text: '在 GitHub 上编辑此页',
      },
      docFooter: { prev: '上一篇', next: '下一篇' },
      lastUpdated: { text: '最后更新' },
      returnToTopLabel: '回到顶部',
      sidebarMenuLabel: '目录',
      footer: {
        message: '基于 Apache-2.0 开源，克隆即用',
        copyright: 'LubanAgent Contributors',
      },
      nav: [
        { text: '首页', link: '/', activeMatch: '^/$' },
        { text: '教程', link: '/tutorial/', activeMatch: '/tutorial/' },
        { text: '使用手册', link: '/manual/', activeMatch: '/manual/' },
        {
          text: '更多',
          items: [
            { text: 'GitHub 仓库', link: 'https://github.com/lozsh/LuBan' },
            { text: '问题反馈', link: 'https://github.com/lozsh/LuBan/issues' },
            { text: '发布日志', link: 'https://github.com/lozsh/LuBan/releases' },
          ],
        },
      ],
    },
    // fastdom 是 CJS 包（mermaid 依赖链引入），dev 模式必须显式预打包，
    // 否则直接按源码伺服会报 "does not provide an export named 'default'" 导致整站白屏（生产构建不受影响）。
    // mermaid 本体也必须一起预打包：它动态加载，若按源码伺服，其内部 chunk 对 fastdom 的裸引用
    // 不会被重写到优化产物，同样触发上述白屏
    vite: {
      optimizeDeps: {
        include: [
          'mermaid',
          'fastdom',
          'fastdom/extensions/fastdom-promised',
        ],
      },
    },
    mermaid: {},
  }),
);
