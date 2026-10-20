import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import TracesPage from './TracesPage';

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/traces']}>
        <Routes>
          <Route path="/traces" element={<TracesPage />} />
          <Route path="/traces/:runId" element={<div>回放页</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Trace 列表', () => {
  it('渲染 Run 表：run_id / agent / 状态徽章 / 模型 / 用量 / 结束原因', async () => {
    renderPage();

    // fixture 三条 Run
    expect(await screen.findByText('run-0001')).toBeInTheDocument();
    expect(screen.getByText('run-0002')).toBeInTheDocument();
    expect(screen.getByText('run-0003')).toBeInTheDocument();

    // 状态徽章
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('已取消')).toBeInTheDocument();

    // 实际模型与结束原因
    expect(screen.getAllByText('deepseek-chat').length).toBe(2);
    expect(screen.getByText('qwen-plus')).toBeInTheDocument();
    expect(screen.getByText('final_answer')).toBeInTheDocument();

    // 会话 ID / 用户列：有值展示，空值 —
    expect(screen.getByText('sess-1001')).toBeInTheDocument();
    expect(screen.getByText('sess-1002')).toBeInTheDocument();
    expect(screen.getByText('user-1001')).toBeInTheDocument();
    expect(screen.getByText('user-1002')).toBeInTheDocument();
    const emptyCells = screen
      .getAllByText('—')
      .map((cell) => cell.textContent);
    expect(emptyCells).toContain('—'); // run-0003 无会话/用户
  });

  it('点击行进入详情回放', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText('run-0001'));
    expect(await screen.findByText('回放页')).toBeInTheDocument();
  });
});
