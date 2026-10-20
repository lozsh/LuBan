import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import KnowledgePage from '@/pages/knowledge/KnowledgePage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter([{ path: '/knowledge', element: <KnowledgePage /> }], {
    initialEntries: ['/knowledge'],
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('KnowledgePage（/knowledge 知识库）', () => {
  it('渲染知识源列表：托管源带参数、内置源显内置徽标且无操作', async () => {
    renderPage();
    // 托管源 team-wiki（来自 MSW fixtures）
    expect(await screen.findByText('team-wiki')).toBeInTheDocument();
    expect(screen.getByText('团队 wiki 文档')).toBeInTheDocument();
    // 托管源切块参数 500/50
    expect(screen.getByText('500/50')).toBeInTheDocument();
    // 内置源 product-faq 徽标
    expect(screen.getByText('内置')).toBeInTheDocument();
    // 重新切块仅托管源有（team-wiki 一个）
    expect(screen.getAllByRole('button', { name: '重新切块' })).toHaveLength(1);
    // 检索测试面板
    expect(screen.getByRole('heading', { name: '知识库' })).toBeInTheDocument();
    expect(screen.getByText('检索测试')).toBeInTheDocument();
  });

  it('点上传文档打开 dialog：名称/参数/文件校验', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /上传文档/ }));
    expect(
      await screen.findByRole('heading', { name: '上传文档创建知识源' }),
    ).toBeInTheDocument();
    // 无文件时上传禁用
    const submit = screen.getByRole('button', { name: '上传' });
    expect(submit).toBeDisabled();
    // 非法名称提示
    await user.type(screen.getByLabelText(/知识源名/), 'Bad Name');
    expect(await screen.findByText(/名称须为 2-64 位/)).toBeInTheDocument();
    expect(submit).toBeDisabled();
  });

  it('点切块打开切块详情 dialog', async () => {
    const user = userEvent.setup();
    renderPage();
    // 两个知识源各有"切块"按钮，点第一个（team-wiki）
    const chunkButtons = await screen.findAllByRole('button', { name: '切块' });
    await user.click(chunkButtons[0]);
    expect(await screen.findByText(/team-wiki 切块/)).toBeInTheDocument();
    // 切块内容来自 MSW fixtures
    expect(await screen.findByText(/知识切块 0/)).toBeInTheDocument();
    expect(screen.getAllByText('onboarding.md').length).toBeGreaterThan(0);
  });

  it('检索测试：输入查询返回命中块与分数', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText(/查询/), '退货政策');
    await user.click(screen.getByRole('button', { name: /检索/ }));
    // MSW knowledgeHits 第一条内容 + 分数徽标
    expect(
      await screen.findByText(/退货政策支持七天无理由退货，退货运费由商家承担/),
    ).toBeInTheDocument();
    expect(await screen.findByText('0.412')).toBeInTheDocument();
  });
});
