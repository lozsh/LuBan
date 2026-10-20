import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { server } from '@/mocks/node';
import { agentVersions } from '@/mocks/fixtures';

import AgentEditorPage from '@/pages/agents/AgentEditorPage';

function renderPage(key = 'cs-agent') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      { path: '/agents/:key', element: <AgentEditorPage /> },
      { path: '/agents/:key/debug', element: <div>agent-debug-stub</div> },
    ],
    { initialEntries: [`/agents/${key}`] },
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('AgentEditorPage（/agents/:key 编辑器）', () => {
  it('加载服务端定义并回填表单（身份区 key 只读、名称/指令回填）', async () => {
    renderPage();
    expect(await screen.findByText('Agent 定义编辑器 — cs-agent')).toBeInTheDocument();

    const keyInput = screen.getByDisplayValue('cs-agent');
    expect(keyInput).toBeDisabled();
    expect(screen.getByDisplayValue('智能客服')).toBeInTheDocument();

    // 版本侧栏标注当前版 v3
    const sidebar = screen.getByText('版本历史').parentElement!.parentElement!;
    expect(within(sidebar).getByText('当前版')).toBeInTheDocument();
    expect(within(sidebar).getByText('v3')).toBeInTheDocument();

    // 指令区回填
    await userEvent.setup().click(screen.getByRole('tab', { name: '指令' }));
    expect(
      screen.getByDisplayValue('你是 {company} 的客服专员，先安抚再解决，退款必须走审批。'),
    ).toBeInTheDocument();
  });

  it('保存 = 新建版本：触发 POST /v1/agents/{key}/versions，侧栏出现 v4 当前版', async () => {
    const user = userEvent.setup();
    renderPage();

    const nameInput = await screen.findByDisplayValue('智能客服');
    await user.clear(nameInput);
    await user.type(nameInput, '智能客服Pro');

    await user.click(screen.getByRole('button', { name: /保存为新版本/ }));

    expect(await screen.findByText(/已保存版本 v4/)).toBeInTheDocument();

    // 侧栏出现 v4 且标注当前版；点击 v1 可查看历史只读快照
    const sidebar = screen.getByText('版本历史').parentElement!.parentElement!;
    const v4 = within(sidebar).getByText('v4');
    expect(within(v4).getByText('当前版')).toBeInTheDocument();

    await user.click(within(sidebar).getByText('v1'));
    expect(await screen.findByText('v1 只读快照')).toBeInTheDocument();
  });

  it('形象卡：预览当前形象，选文件上传即 PUT avatar 端点', async () => {
    // jsdom 下 undici fetch 序列化 FormData body 挂起（multipart 到不了 MSW handler，
    // 既有环境坑，知识库上传同款），fetch 层桩拦截 avatar PUT 直接应答
    const uploads: Array<{ key: string; filename: string }> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'PUT' && /\/v1\/agents\/.+\/avatar$/.test(url)) {
        const form = init.body as FormData;
        const file = form.get('file');
        uploads.push({
          key: url.split('/').slice(-2, -1)[0],
          filename: file instanceof File ? file.name : '',
        });
        return new Response(JSON.stringify({ key: 'cs-agent', avatar: 'avatar.svg', version: 4 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return originalFetch(input as Parameters<typeof originalFetch>[0], init);
    }) as typeof fetch;

    const user = userEvent.setup();
    try {
      renderPage();
      await screen.findByText('Agent 定义编辑器 — cs-agent');

      // 身份 Tab 默认展开：形象卡可见，cs-agent 已有形象（mock fixtures avatar.svg）
      // H13：头像经鉴权 fetch → blob URL 异步加载，用 findBy 等待
      const card = screen.getByTestId('avatar-card');
      expect(await within(card).findByTestId('agent-avatar-img')).toBeInTheDocument();
      expect(within(card).getByText('avatar.svg')).toBeInTheDocument();

      // 未选文件时上传禁用
      expect(screen.getByRole('button', { name: '上传' })).toBeDisabled();

      // 选择文件并上传：multipart PUT /v1/agents/{key}/avatar
      const file = new File(['<svg/>'], 'new-mascot.svg', { type: 'image/svg+xml' });
      await user.upload(screen.getByLabelText('形象文件'), file);
      await user.click(screen.getByRole('button', { name: '上传' }));

      await waitFor(() =>
        expect(uploads).toEqual([{ key: 'cs-agent', filename: 'new-mascot.svg' }]),
      );

      // 上传即版本：返回的 version/avatar 回写草稿，保存按钮显示下一个版本号（防 409）
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /保存为新版本/ })).toHaveTextContent('v5'),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe('Agent 编辑器 — 知识防错答守门字段', () => {
  it('记忆与知识 Tab 渲染守门三字段：阈值 / 兜底话术 / 时效，可编辑并随保存提交', async () => {
    // 版本账本是模块级共享状态：清掉前序用例写入的 v4，避免保存撞 409 版本冲突
    agentVersions['cs-agent'] = (agentVersions['cs-agent'] ?? []).filter((v) => v.version <= 3);

    const bodies: { knowledge?: Record<string, unknown> }[] = [];
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST' && /\/v1\/agents\/.+\/versions$/.test(request.url)) {
        bodies.push((await request.clone().json()) as { knowledge?: Record<string, unknown> });
      }
    });

    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Agent 定义编辑器 — cs-agent');
    await user.click(screen.getByRole('tab', { name: '记忆与知识' }));

    // Field 的 Label 未用 htmlFor 关联，按标签文本定位字段容器再取内部输入
    const threshold = screen.getByText(/相关度阈值/).parentElement!.querySelector('input')!;
    expect(threshold).toHaveValue(0);
    await user.clear(threshold);
    await user.type(threshold, '0.2');

    const fallback = screen.getByText(/拒答兜底话术/).parentElement!.querySelector('textarea')!;
    await user.type(fallback, '知识库未覆盖该问题。');

    const maxAge = screen.getByText(/知识时效/).parentElement!.querySelector('input')!;
    expect(maxAge).toHaveValue(null);
    await user.type(maxAge, '30');

    await user.click(screen.getByRole('button', { name: /保存为新版本/ }));
    expect(await screen.findByText(/已保存版本 v4/)).toBeInTheDocument();

    // 提交体带守门字段（版本 POST 携带编辑后的 knowledge 段）
    expect(bodies.length).toBeGreaterThan(0);
    const knowledge = bodies[bodies.length - 1].knowledge;
    expect(knowledge?.score_threshold).toBe(0.2);
    expect(knowledge?.no_hit_fallback).toBe('知识库未覆盖该问题。');
    expect(knowledge?.max_age_days).toBe(30);
  });
});
