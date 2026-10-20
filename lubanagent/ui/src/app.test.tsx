import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { routeObjects } from '@/routes';

function renderAt(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter(routeObjects, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('布局壳', () => {
  it('左侧导航 + 顶栏渲染（mock 数据经 MSW 注入）', async () => {
    renderAt('/agents');
    for (const label of [
      'Agents',
      '调试台',
      '用户端聊天',
      'Prompt 库',
      '模型',
      '工具',
      'Skills',
      '知识库',
      '流程图',
      'Traces',
      '评测',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('开发者工作台')).toBeInTheDocument();
    // useHealth → MSW /health
    expect(await screen.findByText('live')).toBeInTheDocument();
  });

  it('/ 重定向到 /agents', async () => {
    renderAt('/');
    expect(await screen.findByText('Agent 列表')).toBeInTheDocument();
  });
});

describe('14 条页面路由均可达', () => {
  const cases: Array<[string, string]> = [
    ['/agents', 'Agent 列表'],
    ['/agents/cs-agent', 'Agent 定义编辑器 — cs-agent'],
    ['/agents/cs-agent/debug', '运行调试台'],
    ['/chat', 'LubanAgent 聊天'],
    ['/prompts', 'Prompt 库'],
    ['/models', '模型中心'],
    ['/models/compare', '模型对比'],
    ['/tools', 'Tool 中心'],
    ['/skills', 'Skill 管理'],
    ['/knowledge', '知识库'],
    ['/graphs', '流程图'],
    ['/traces', 'Trace 列表'],
    ['/traces/run-0001', 'Trace 回放'],
    ['/evals', '评测报告'],
  ];

  it.each(cases)('%s → %s', async (path, title) => {
    renderAt(path);
    // 全量并行跑时页面首渲染 + mock 请求可能超过 findBy 默认 1s；
    // 按 heading 断言避免与侧栏同名导航项歧义（如"Prompt 调试"）
    expect(
      await screen.findByRole('heading', { name: title }, { timeout: 5000 }),
    ).toBeInTheDocument();
  });
});
