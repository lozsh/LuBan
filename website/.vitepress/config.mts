import { defineConfig } from 'vitepress';
import { withMermaid } from 'vitepress-plugin-mermaid';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { ElementContent } from 'hast';
import type { ShikiTransformer } from 'shiki';

type CommentRange = { start: number; end: number };
const COMMENT_STATE = Symbol('luban:copy-comments');
type CommentState = { [COMMENT_STATE]?: CommentRange[][] };

/** 这些注释承载执行、静态检查或许可信息，不能当成解释文字删掉。 */
const PRESERVED_COMMENT = /#!|coding\s*[:=]|SPDX-|copyright|licensed\s+under|@license|@preserve|@ts-|type\s*:|noqa\b|pylint\s*:|pyright\s*:|mypy\s*:|ruff\s*:|eslint|tslint|stylelint|prettier|fmt\s*:|isort\s*:|shellcheck\b|go:|noinspection|istanbul\s+ignore|c8\s+ignore|[@#]__PURE__|webpack\w*\s*:|sourceMappingURL=|sourceURL=/i;
const LICENSE_COMMENT = /SPDX-|copyright|\blicen[cs]e(?:d)?\b|@preserve/i;

function nodeText(node: ElementContent): string {
  if (node.type === 'text') return node.value;
  return node.type === 'element' ? node.children.map(nodeText).join('') : '';
}

/** 分割高亮节点时保留样式，只改变复制忽略区域，不改文档里的代码。 */
function sliceNodes(nodes: ElementContent[], start: number, end: number): ElementContent[] {
  const result: ElementContent[] = [];
  let offset = 0;
  for (const node of nodes) {
    const length = nodeText(node).length;
    const from = Math.max(0, start - offset);
    const to = Math.min(length, end - offset);
    if (from < to) {
      if (node.type === 'text') result.push({ ...node, value: node.value.slice(from, to) });
      else if (node.type === 'element') {
        result.push({ ...node, properties: { ...node.properties }, children: sliceNodes(node.children, from, to) });
      }
    }
    offset += length;
  }
  return result;
}

/** 使用词法作用域而非颜色或“# / //”正则；字符串、heredoc、YAML 提示词均保留。 */
export const inlineCommentCopyTransformer: ShikiTransformer = {
  name: 'luban:copy-without-explanatory-comments',
  preprocess(source) {
    const { tokens } = this.codeToTokens(source, {
      ...this.options,
      includeExplanation: 'scopeName',
    });
    const comments: (CommentRange & { block: boolean })[] = [];
    for (const line of tokens) {
      for (const token of line) {
        let offset = token.offset;
        for (const part of token.explanation || []) {
          const scopes = part.scopes.map((scope) => scope.scopeName);
          const commentIndex = scopes.findIndex((scope) => scope.startsWith('comment.'));
          const literalIndex = scopes.findIndex((scope) => scope.startsWith('string.'));
          if (commentIndex >= 0 && (literalIndex < 0 || literalIndex > commentIndex)) {
            const block = scopes[commentIndex].includes('.block');
            const previous = comments[comments.length - 1];
            if (previous && previous.block === block &&
                (previous.end === offset || (block && /^[\r\n]+$/.test(source.slice(previous.end, offset))))) {
              previous.end = offset + part.content.length;
            } else {
              comments.push({ start: offset, end: offset + part.content.length, block });
            }
          }
          offset += part.content.length;
        }
      }
    }
    // 行注释许可证往往跨多行；命中许可声明时保留整个连续独占行注释组。
    const licenseRanges = new Set<CommentRange>();
    let group: typeof comments = [];
    const finishGroup = () => {
      if (group.some((range) => LICENSE_COMMENT.test(source.slice(range.start, range.end)))) {
        group.forEach((range) => licenseRanges.add(range));
      }
      group = [];
    };
    for (const range of comments) {
      const lineStart = source.lastIndexOf('\n', range.start - 1) + 1;
      const lineEnd = source.indexOf('\n', range.end);
      const standalone = !range.block && !source.slice(lineStart, range.start).trim() &&
        !source.slice(range.end, lineEnd < 0 ? source.length : lineEnd).trim();
      if (!standalone) {
        finishGroup();
        continue;
      }
      const previous = group[group.length - 1];
      if (previous && !/^[\t ]*\r?\n[\t ]*$/.test(source.slice(previous.end, range.start))) finishGroup();
      group.push(range);
    }
    finishGroup();
    const explanatory = comments.filter((range) => !licenseRanges.has(range) &&
      !PRESERVED_COMMENT.test(source.slice(range.start, range.end)));
    let offset = 0;
    (this.meta as CommentState)[COMMENT_STATE] = source.split('\n').map((line) => {
      const ranges = explanatory
        .filter((range) => range.start < offset + line.length && range.end > offset)
        .map((range) => ({ start: Math.max(0, range.start - offset), end: Math.min(line.length, range.end - offset) }));
      offset += line.length + 1;
      return ranges;
    });
  },
  code(code) {
    const rangesByLine = (this.meta as CommentState)[COMMENT_STATE] || [];
    const ignoredLines = new Set<ElementContent>();
    this.lines.forEach((line, index) => {
      // VitePress 的 diff / highlight 注解先处理；不碰已被它移除的行。
      if (!code.children.includes(line)) return;
      const text = nodeText(line);
      const ranges = (rangesByLine[index] || [])
        .map((range) => ({ start: range.start, end: Math.min(range.end, text.length) }))
        .filter((range) => range.start < range.end);
      if (!ranges.length) return;
      const kept = ranges.reduce((value, range) => value.slice(0, range.start) + ' '.repeat(range.end - range.start) + value.slice(range.end), text);
      if (!kept.trim()) {
        this.addClassToHast(line, ['vp-copy-ignore', 'docs-code-comment']);
        ignoredLines.add(line);
        return;
      }
      const children = line.children;
      const result: ElementContent[] = [];
      let offset = 0;
      for (const range of ranges) {
        const tail = !text.slice(range.end).trim();
        const gap = tail ? (text.slice(offset, range.start).match(/[\t ]+$/)?.[0].length || 0) : 0;
        const start = range.start - gap;
        const end = tail ? text.length : range.end;
        result.push(...sliceNodes(children, offset, start));
        // 行中块注释也可能是词法分隔符；删除后不得把两个标识符粘在一起。
        if (!tail && start > 0 && end < text.length && !/\s/.test(text[start - 1]) && !/\s/.test(text[end])) {
          result.push({ type: 'text', value: ' ' });
        }
        result.push({
          type: 'element', tagName: 'span',
          properties: {
            class: `vp-copy-ignore docs-code-comment${tail ? ' docs-inline-comment' : ''}`,
            title: '解释注释，复制代码时不包含',
          },
          children: sliceNodes(children, start, end),
        });
        offset = end;
      }
      result.push(...sliceNodes(children, offset, text.length));
      line.children = result;
    });
    // 注释独占一行时，连同分隔换行一起忽略，避免复制出一串空白行。
    code.children = code.children.map((node, index, nodes) => {
      if (node.type === 'text' && node.value === '\n' &&
          (ignoredLines.has(nodes[index - 1]) || (index === nodes.length - 2 && ignoredLines.has(nodes[index + 1])))) {
        return { type: 'element', tagName: 'span', properties: { class: 'vp-copy-ignore' }, children: [node] };
      }
      return node;
    });
  },
};

// GitHub Pages 项目页部署时用 DOCS_BASE=/<repo>/ npm run build 覆盖
const base = process.env.DOCS_BASE || '/';
const logo = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" rx="12" fill="#0f766e"/><path d="M14 12v24h20v-7H21V12Z" fill="#fff"/><path d="M28 12h6v10h-6Z" fill="#99f6e4"/></svg>`;
const logoUrl = `data:image/svg+xml,${encodeURIComponent(logo)}`;

// 侧栏配置：必须放在 themeConfig.sidebar（默认主题从 theme 读取 sidebar），
// 写在 defineConfig 顶层会被静默忽略，导致全站文章页没有左侧菜单
const sidebar = {
  // ------------------------------------------------ 教程（线性课程）
  '/tutorial/': [
    {
      text: '开发指南',
      items: [{ text: '开发指南导航', link: '/tutorial/' }],
    },
    {
      text: '基础使用',
      collapsed: false,
      items: [
        { text: '认识 Agent', link: '/tutorial/00-primer' },
        { text: '跑起来', link: '/tutorial/01-setup-and-first-run' },
        { text: '第一个自己的 Agent', link: '/tutorial/02-first-agent' },
      ],
    },
    {
      text: '能力扩展',
      collapsed: false,
      items: [
        { text: '接真实模型', link: '/tutorial/03-real-models' },
        { text: '写自己的 Tool', link: '/tutorial/04-write-a-tool' },
        { text: '给 Agent 加知识', link: '/tutorial/05-knowledge-rag' },
        { text: '用评测保质量', link: '/tutorial/06-evals' },
      ],
    },
    {
      text: '流程与团队',
      collapsed: false,
      items: [{ text: '确定性流程与多 Agent', link: '/tutorial/07-graphs-and-teams' }],
    },
    {
      text: '部署运维',
      collapsed: false,
      items: [{ text: '部署上生产', link: '/tutorial/08-deploy' }],
    },
    {
      text: '接入与验收',
      collapsed: false,
      items: [{ text: '业务接入与验收', link: '/tutorial/09-graduation' }],
    },
  ],
  // ------------------------------------------------ 使用手册（字典式）
  '/manual/': [
    {
      text: '开始',
      collapsed: false,
      items: [
        { text: '项目介绍', link: '/manual/introduction' },
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
    description: '面向开发者的开源 Agent 工程基座：项目介绍、开发指南、功能与 API 手册',
    base,
    lastUpdated: true,
    markdown: {
      codeTransformers: [inlineCommentCopyTransformer],
    },
    async buildEnd(site) {
      // robots.txt 只有部署到域名根目录才是标准爬虫入口；项目子路径下仅作同内容声明。
      await writeFile(resolve(site.outDir, 'robots.txt'), [
        '# Hosted documentation: online reading only; no automated crawling or mirroring requested.',
        '# AI training and deliberate local export are not permitted by the site usage request.',
        '# Source code and repository documentation remain licensed under Apache-2.0.',
        '# Robots directives are voluntary, not access control.',
        'User-agent: *',
        `Disallow: ${base}`,
        '',
      ].join('\n'), 'utf8');
    },
    ignoreDeadLinks: [
      // 本机服务地址（localhost:8000/5173/3000 等）是操作示例里的目标地址，不是站内链接
      /^https?:\/\/localhost/,
    ],
    head: [
      // 内联 SVG favicon，避免 favicon.ico 404
      ['link', { rel: 'icon', type: 'image/svg+xml', href: logoUrl }],
      // 社交分享元信息（对齐 Vite 官网做法）
      ['meta', { property: 'og:type', content: 'website' }],
      ['meta', { property: 'og:title', content: 'LubanAgent 文档' }],
      ['meta', { property: 'og:site_name', content: 'LubanAgent' }],
      ['meta', { property: 'og:description', content: '开源 Agent 工程基座：完整源码、模型接入、多 Agent、监控与部署' }],
      ['meta', { name: 'theme-color', content: '#0f766e' }],
      // noai / noimageai 是非标准使用意愿声明，不能替代鉴权或阻止下载。
      ['meta', { name: 'robots', content: 'noindex, nofollow, noarchive, noai, noimageai' }],
    ],
    themeConfig: {
      sidebar,
      siteTitle: 'LubanAgent',
      logo: { src: logoUrl, alt: 'LubanAgent' },
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
      socialLinks: [{ icon: 'github', link: 'https://github.com/lubanagent-dev/LuBan' }],
      outline: { level: [2, 3], label: '本页目录' },
      editLink: {
        pattern: 'https://github.com/lubanagent-dev/LuBan/edit/master/website/:path',
        text: '在 GitHub 上编辑此页',
      },
      docFooter: { prev: '上一篇', next: '下一篇' },
      lastUpdated: { text: '最后更新' },
      returnToTopLabel: '回到顶部',
      sidebarMenuLabel: '目录',
      footer: {
        message: '源码与仓库文档按 Apache-2.0 开源；本站网页供在线查阅',
        copyright: 'LubanAgent Contributors',
      },
      nav: [
        { text: '首页', link: '/', activeMatch: '^/$' },
        { text: '项目介绍', link: '/manual/introduction', activeMatch: '^/manual/introduction$' },
        { text: '快速开始', link: '/manual/getting-started', activeMatch: '^/manual/getting-started$' },
        { text: '开发指南', link: '/tutorial/', activeMatch: '^/tutorial/' },
        // 两个独立入口也位于 manual/，不能让它们同时命中“使用手册”。
        { text: '使用手册', link: '/manual/', activeMatch: '^/manual/(?!(?:introduction|getting-started)$)' },
        {
          text: '更多',
          items: [
            { text: 'GitHub 仓库', link: 'https://github.com/lubanagent-dev/LuBan' },
            { text: '问题反馈', link: 'https://github.com/lubanagent-dev/LuBan/issues' },
            { text: '发布日志', link: 'https://github.com/lubanagent-dev/LuBan/releases' },
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
