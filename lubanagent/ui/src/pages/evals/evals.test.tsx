import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import EvalsPage from './EvalsPage';

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EvalsPage />
    </QueryClientProvider>,
  );
}

describe('评测报告', () => {
  it('报告列表渲染：数据集/Agent/版本/模型/通过率/错答率', async () => {
    renderPage();
    expect(await screen.findByText('cs-regression-v3')).toBeInTheDocument();
    expect(screen.getByText('cs-regression-v4')).toBeInTheDocument();
    expect(screen.getAllByText('cs-agent').length).toBe(2);
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.getByText('50% (2/4)')).toBeInTheDocument();
    expect(screen.getByText('75% (3/4)')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument(); // 错答率（v4 报告）
    expect(screen.getAllByText('fake-chat').length).toBeGreaterThanOrEqual(2);
  });

  it('详情：逐 case 结果 + 四类断言图标 + verdict detail 展开 + run_id', async () => {
    renderPage();
    const user = userEvent.setup();

    // 打开 cs-regression-v4 报告详情
    const row = (await screen.findByText('cs-regression-v4')).closest('tr');
    expect(row).not.toBeNull();
    await user.click(within(row as HTMLTableRowElement).getByRole('button', { name: '查看' }));

    // 逐 case 表格（四类断言列头）
    expect(await screen.findByText('refund-approval-flow')).toBeInTheDocument();
    expect(screen.getByText('policy-out-of-scope')).toBeInTheDocument();
    for (const column of ['输出断言', '工具断言', 'judge 评分', '拒答断言']) {
      expect(screen.getAllByText(column).length).toBeGreaterThanOrEqual(1);
    }
    // pass/fail 图标（v4 报告 3 pass / 1 fail，每 case 2-3 个 verdict 图标）
    expect(screen.getAllByLabelText('通过').length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByLabelText('未通过').length).toBeGreaterThanOrEqual(2);

    // 展开 verdict detail + run_id
    await user.click(screen.getByRole('button', { name: '展开 policy-out-of-scope' }));
    expect(await screen.findByText(/应拒答未拒答（错答）/)).toBeInTheDocument();
    expect(screen.getByText(/judge 评分 2\/4/)).toBeInTheDocument();
    expect(screen.getByText('run_id：run-0004')).toBeInTheDocument();
  });
});
