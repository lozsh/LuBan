import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import SkillsPage from '@/pages/skills/SkillsPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter([{ path: '/skills', element: <SkillsPage /> }], {
    initialEntries: ['/skills'],
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('SkillsPage（/skills Skill 管理）', () => {
  it('渲染 Skill 列表 + 上传按钮 + 已分配 Agent 数', async () => {
    renderPage();
    expect(await screen.findByText('客服话术手册')).toBeInTheDocument();
    expect(screen.getByText('SQL 生成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /上传 Skill 包/ })).toBeInTheDocument();
    expect(screen.getAllByText('分配').length).toBeGreaterThan(0);
  });

  it('点上传按钮打开上传 dialog', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /上传 Skill 包/ }));
    expect(await screen.findByRole('heading', { name: '上传 Skill 包' })).toBeInTheDocument();
    expect(screen.getByText(/zip 包内须含一个目录/)).toBeInTheDocument();
  });

  it('点分配按钮打开 dialog + 已分配 Agent 预勾选', async () => {
    const user = userEvent.setup();
    renderPage();
    const assignButtons = await screen.findAllByRole('button', { name: '分配' });
    await user.click(assignButtons[0]); // cs-playbook（首个 Skill，被智能客服引用）
    expect(
      await screen.findByText(/分配 Skill「cs-playbook」到 Agent/),
    ).toBeInTheDocument();
    // 智能客服 agent 引用了 cs-playbook → 进入时预勾选
    const checkbox = screen.getByRole('checkbox', { name: /智能客服/ });
    expect(checkbox).toBeChecked();
  });
});
