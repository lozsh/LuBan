import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import AgentsPage from '@/pages/agents/AgentsPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      { path: '/agents', element: <AgentsPage /> },
      { path: '/agents/:key', element: <div>agent-editor-stub</div> },
      { path: '/agents/:key/debug', element: <div>agent-debug-stub</div> },
    ],
    { initialEntries: ['/agents'] },
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('AgentsPage（/agents 列表）', () => {
  it('渲染 mock Agent 卡片：名称/状态徽章/当前版本', async () => {
    renderPage();
    expect(await screen.findByText('智能客服')).toBeInTheDocument();
    expect(screen.getByText('代码助手')).toBeInTheDocument();
    expect(screen.getByText('已发布')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.getByText('cs-agent')).toBeInTheDocument();
  });

  it('新建 Agent：dialog 提交触发 POST /v1/agents，成功后跳转编辑器', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole('button', { name: /新建 Agent/ }));
    expect(await screen.findByText('创建草稿定义（v1），创建后进入编辑器补全能力并保存版本。')).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Key（唯一/), 'order-bot');
    await user.type(screen.getByLabelText('名称'), '订单机器人');
    await user.type(screen.getByLabelText('描述'), '处理订单咨询');
    await user.click(screen.getByRole('button', { name: '创建' }));

    // 成功后 navigate 到 /agents/order-bot（stub 路由）
    expect(await screen.findByText('agent-editor-stub')).toBeInTheDocument();
  });
});
