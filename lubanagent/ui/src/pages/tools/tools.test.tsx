import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import ToolsPage from './ToolsPage';

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToolsPage />
    </QueryClientProvider>,
  );
}

describe('Tool 中心', () => {
  it('工具表渲染：风险徽章/超时/需审批', async () => {
    renderPage();
    expect(await screen.findByText('query-order')).toBeInTheDocument();
    expect(screen.getByText('refund')).toBeInTheDocument();
    expect(screen.getByText('高风险')).toBeInTheDocument();
    // 表格行内徽章 + 图标 sr-only 文案同名
    expect(screen.getAllByText('需审批').length).toBeGreaterThanOrEqual(1);
  });

  it('在线执行：schema 表单渲染 → 必填校验 → 执行结果面板', async () => {
    renderPage();
    const user = userEvent.setup();

    // 展开 query-order 详情
    await user.click(await screen.findByRole('button', { name: '测试 query-order' }));

    // args_schema → 表单（order_id 必填标星）
    const orderInput = await screen.findByLabelText(/order_id/);
    expect(orderInput).toBeInTheDocument();

    // 缺必填 → UX 层拦截
    await user.click(screen.getByRole('button', { name: '在线执行' }));
    expect(await screen.findByText(/缺少必填参数: order_id/)).toBeInTheDocument();

    // 填参执行 → 结果面板（mock 响应）
    await user.type(orderInput, '12345');
    await user.click(screen.getByRole('button', { name: '在线执行' }));

    expect(await screen.findByText('success')).toBeInTheDocument();
    expect(screen.getByText(/mock 执行结果/)).toBeInTheDocument();
    expect(screen.getByText('耗时 118ms')).toBeInTheDocument();
    expect(screen.getByText('尝试 1 次')).toBeInTheDocument();
  });
});
