import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import GraphsPage from '@/pages/graphs/GraphsPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter([{ path: '/graphs', element: <GraphsPage /> }], {
    initialEntries: ['/graphs'],
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('GraphsPage（/graphs 流程图只读拓扑）', () => {
  it('渲染图列表（名称 + 描述）', async () => {
    renderPage();
    expect(await screen.findByText('refund_flow')).toBeInTheDocument();
    expect(screen.getByText(/退款流水线：校验→计算→审批→退款→通知/)).toBeInTheDocument();
  });

  it('选中图后渲染节点拓扑：全部节点名 + START/END 徽标 + 人工审批 prompt', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByText('refund_flow'));
    expect(await screen.findByText('check_eligibility')).toBeInTheDocument();
    expect(screen.getByText('compute_amount')).toBeInTheDocument();
    expect(screen.getByText('execute_refund')).toBeInTheDocument();
    expect(screen.getByText('notify')).toBeInTheDocument();
    expect(screen.getByText('START')).toBeInTheDocument();
    expect(screen.getByText('END')).toBeInTheDocument();
    expect(screen.getByText(/退款金额 >500 元，需要人工审批/)).toBeInTheDocument();
    // step 节点显示 docstring 摘要（中文含义），不再是裸 "Step"
    expect(screen.getByText(/资格校验：订单存在且可退/)).toBeInTheDocument();
    expect(screen.getByText(/通知：构造用户可见的退款结果摘要/)).toBeInTheDocument();
    expect(screen.queryByText('Step')).not.toBeInTheDocument();
  });

  it('未选中时显示引导文案', async () => {
    renderPage();
    expect(await screen.findByText('refund_flow')).toBeInTheDocument();
    expect(screen.getByText(/从左侧选择一个流程图/)).toBeInTheDocument();
  });
});
