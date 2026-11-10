import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { diagnosticLogs } from '@/mocks/fixtures';
import { server } from '@/mocks/node';
import { routeObjects } from '@/routes';

function renderDiagnostics() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routeObjects, { initialEntries: ['/diagnostics'] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

function renderDiagnosticsAt(entry: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routeObjects, { initialEntries: [entry] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('日志排查页', () => {
  it('展示 Run、最终答案和关联程序日志', async () => {
    renderDiagnostics();
    expect(await screen.findByRole('heading', { name: '日志排查' })).toBeInTheDocument();
    expect(
      await screen.findByText('我的订单 12345 到哪了？', {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '程序日志（3）' })).toBeInTheDocument();
    expect(screen.getByText('第 1 页')).toBeInTheDocument();
  });

  it('日志过滤使用后端字段，且 Run 查询不携带日志专属参数', async () => {
    const runUrls: URL[] = [];
    const logUrls: URL[] = [];
    server.use(
      http.get('*/v1/diagnostics/runs', ({ request }) => {
        runUrls.push(new URL(request.url));
        return HttpResponse.json({
          items: [],
          total: 0,
          limit: 20,
          offset: 0,
        });
      }),
      http.get('*/v1/logs', ({ request }) => {
        logUrls.push(new URL(request.url));
        return HttpResponse.json({
          items: diagnosticLogs,
          total: diagnosticLogs.length,
          limit: 20,
          offset: 0,
          dropped_count: 0,
          write_failures: 0,
        });
      }),
    );
    const user = userEvent.setup();
    renderDiagnostics();
    expect(await screen.findByRole('heading', { name: '日志排查' })).toBeInTheDocument();

    await user.type(screen.getByLabelText('请求 ID'), 'req-0001');
    await user.type(screen.getByLabelText('开始时间（仅日志）'), '2026-07-23T09:58');
    await user.type(screen.getByLabelText('结束时间（仅日志）'), '2026-07-23T09:59');
    await user.click(screen.getByRole('button', { name: '查询' }));

    await waitFor(() =>
      expect(logUrls.some((url) => url.searchParams.get('request_id') === 'req-0001')).toBe(true),
    );
    const latestLogUrl = logUrls.at(-1)!;
    expect(latestLogUrl.searchParams.get('from_time')).toBe(
      new Date('2026-07-23T09:58').toISOString(),
    );
    expect(latestLogUrl.searchParams.get('to_time')).toBe(
      new Date('2026-07-23T09:59').toISOString(),
    );
    const latestRunUrl = runUrls.at(-1)!;
    expect(latestRunUrl.searchParams.has('request_id')).toBe(false);
    expect(latestRunUrl.searchParams.has('logger')).toBe(false);
    expect(latestRunUrl.searchParams.has('from_time')).toBe(false);
  });

  it('两个 Tab 分页互不影响，下一页依据当前 Tab total', async () => {
    const pageLogs = Array.from({ length: 25 }, (_, index) => ({
      ...diagnosticLogs[0],
      id: `page-log-${index}`,
      created_at: '2026-07-23T09:58:12Z',
      root_run_id: 'run-0001',
    }));
    server.use(
      http.get('*/v1/logs', ({ request }) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get('offset') ?? 0);
        return HttpResponse.json({
          items: pageLogs.slice(offset, offset + 20),
          total: pageLogs.length,
          limit: 20,
          offset,
          dropped_count: 0,
          write_failures: 0,
        });
      }),
    );
    const user = userEvent.setup();
    renderDiagnostics();
    expect(await screen.findByRole('tab', { name: '程序日志（25）' })).toBeInTheDocument();

    // Run Tab 只有 4 条：下一页必须保持禁用。
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();

    await user.click(screen.getByRole('tab', { name: /程序日志/ }));
    expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('第 2 页')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /对话 Run/ }));
    expect(screen.getByText('第 1 页')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  });

  it('按会话过滤后进入详情可看到父子 Run、Trace 与程序日志', async () => {
    const user = userEvent.setup();
    renderDiagnostics();
    expect(await screen.findByRole('heading', { name: '日志排查' })).toBeInTheDocument();
    expect(
      await screen.findByText('我的订单 12345 到哪了？', {}, { timeout: 5000 }),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText('会话 ID'), 'sess-1002');
    await user.click(screen.getByRole('button', { name: '查询' }));
    expect(await screen.findByTestId('diagnostic-run-run-0002')).toBeInTheDocument();

    await user.click(screen.getByTestId('diagnostic-run-run-0002'));
    expect(await screen.findByRole('heading', { name: '对话排查详情' })).toBeInTheDocument();
    expect(await screen.findByText(/refund provider timed out/)).toBeInTheDocument();
    expect(screen.getAllByText('run-0002').length).toBeGreaterThan(0);
  });

  it('详情页程序日志走服务端分页：total>500 时按 offset 翻页不丢数据', async () => {
    // total=520：本地 limit=500 切片会静默漏掉第 501 条之后的内容；
    // 服务端分页下 total 来自响应，共 11 页，每次翻页显式携带 offset。
    const allLogs = Array.from({ length: 520 }, (_, index) => ({
      ...diagnosticLogs[0]!,
      id: `bulk-log-${index}`,
      message: `bulk log ${index}`,
      root_run_id: 'run-0001',
    }));
    const logUrls: URL[] = [];
    server.use(
      http.get('*/v1/logs', ({ request }) => {
        const url = new URL(request.url);
        logUrls.push(url);
        const offset = Number(url.searchParams.get('offset') ?? 0);
        return HttpResponse.json({
          items: allLogs.slice(offset, offset + 50),
          total: allLogs.length,
          limit: 50,
          offset,
          dropped_count: 0,
          write_failures: 0,
        });
      }),
    );
    renderDiagnosticsAt('/diagnostics/run-0001');
    expect(await screen.findByRole('heading', { name: '对话排查详情' })).toBeInTheDocument();

    expect(await screen.findByText('第 1 页 / 共 11 页')).toBeInTheDocument();
    expect(await screen.findByText('bulk log 0')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('第 2 页 / 共 11 页')).toBeInTheDocument();
    // 翻页是真正的新请求：limit=50 + offset=50，由服务端返回后续记录。
    await waitFor(() => {
      const latest = logUrls.at(-1)!;
      expect(latest.searchParams.get('offset')).toBe('50');
      expect(latest.searchParams.get('limit')).toBe('50');
    });
    expect(await screen.findByText('bulk log 50')).toBeInTheDocument();
    // 所有日志查询都以解析后的 root run 为准，而不是路由原始 id。
    expect(logUrls.every((url) => url.searchParams.get('root_run_id') === 'run-0001')).toBe(true);
    // 真实后端 limit 上限 200：任何请求都不得超过，否则 422 整页无日志。
    expect(logUrls.every((url) => Number(url.searchParams.get('limit') ?? 0) <= 200)).toBe(true);
  });

  it('详情页日志查询失败时显示可读错误', async () => {
    server.use(
      http.get('*/v1/logs', () =>
        HttpResponse.json(
          { error: { code: 'INTERNAL', message: 'db unavailable' } },
          { status: 500 },
        ),
      ),
    );
    renderDiagnosticsAt('/diagnostics/run-0001');
    expect(await screen.findByRole('heading', { name: '对话排查详情' })).toBeInTheDocument();
    expect(await screen.findByText(/日志加载失败：db unavailable/)).toBeInTheDocument();
  });

  it('日志丢弃与写库/清理失败计数有明确提示', async () => {
    server.use(
      http.get('*/v1/logs', ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json({
          items: [],
          total: 0,
          limit: Number(url.searchParams.get('limit') ?? 50),
          offset: Number(url.searchParams.get('offset') ?? 0),
          dropped_count: 7,
          write_failures: 2,
        });
      }),
    );
    renderDiagnosticsAt('/diagnostics/run-0001');
    expect(await screen.findByRole('heading', { name: '对话排查详情' })).toBeInTheDocument();
    expect(await screen.findByText(/已丢弃 7 条日志；写库\/清理失败 2 次/)).toBeInTheDocument();
    expect(screen.getByText(/队列满或数据库故障时可能丢日志/)).toBeInTheDocument();
  });
});
