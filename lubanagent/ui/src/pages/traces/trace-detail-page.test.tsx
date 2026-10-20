import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import TraceDetailPage from './TraceDetailPage';

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/traces/run-0001']}>
        <Routes>
          <Route path="/traces/:runId" element={<TraceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Trace 回放', () => {
  it('七问摘要卡：Agent 版本 / 实际模型 / 调用次数 / 工具清单 / Token 费用 / 结束原因', async () => {
    renderPage();

    const summary = await screen.findByTestId('trace-summary');
    // Run 记录异步到达：摘要字段用 findBy 等待
    expect(await within(summary).findByText('cs-agent · v3')).toBeInTheDocument();
    expect(within(summary).getByText('deepseek-chat')).toBeInTheDocument();
    expect(within(summary).getByText('2')).toBeInTheDocument(); // 模型调用次数
    expect(within(summary).getByText('query-order')).toBeInTheDocument(); // 工具清单
    expect(within(summary).getByText(/812 \/ 156/)).toBeInTheDocument(); // token
    // H10 用量 schema：第二条 model_call_completed 的 usage_known=false → Token 卡片标注
    expect(within(summary).getByTestId('usage-unknown')).toBeInTheDocument();
    expect(within(summary).getByText('final_answer')).toBeInTheDocument(); // 结束原因
    expect(within(summary).getByText('已完成')).toBeInTheDocument(); // 状态徽章
    // 会话 ID / 用户（run-0001 fixture）
    expect(within(summary).getByText('sess-1001')).toBeInTheDocument();
    expect(within(summary).getByText('user-1001')).toBeInTheDocument();
  });

  it('事件时间线：类型着色分组渲染，点击展开 payload JSON 树', async () => {
    const user = userEvent.setup();
    renderPage();

    const timeline = await screen.findByTestId('trace-timeline');
    // step_index 分组标签
    expect(within(timeline).getByText('步骤 0')).toBeInTheDocument();
    expect(within(timeline).getByText('步骤 1')).toBeInTheDocument();
    expect(within(timeline).getAllByText('Run 级').length).toBeGreaterThan(0);
    // 事件类型 + 摘要
    expect(within(timeline).getByText('tool_call_started')).toBeInTheDocument();
    expect(within(timeline).getAllByText('model_token_delta').length).toBe(2);
    // 相对时间轴（首事件 +0ms）
    expect(within(timeline).getByText('+0ms')).toBeInTheDocument();

    // 点击事件行展开 payload JSON 树
    await user.click(within(timeline).getByText('tool_call_started'));
    expect(await screen.findByText('order_id:')).toBeInTheDocument();
    expect(screen.getByText('"12345"')).toBeInTheDocument();
  });
});
