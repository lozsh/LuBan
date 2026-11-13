import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { StrictMode, useState } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { server } from '@/mocks/node';
import { agents, agentVersions } from '@/mocks/fixtures';

import AgentEditorPage from '@/pages/agents/AgentEditorPage';
import { AgentForm } from '@/pages/agents/components/agent-form';
import { CreateAgentDialog } from '@/pages/agents/components/create-agent-dialog';
import type { AgentDefinition, BrainProfileOut } from '@/api/hooks';
import { normalizeDefinition } from '@/pages/agents/lib/agent-form';

function renderPage(
  key = 'cs-agent',
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  const router = createMemoryRouter(
    [
      { path: '/agents/:key', element: <AgentEditorPage /> },
      { path: '/agents/:key/debug', element: <div>agent-debug-stub</div> },
    ],
    { initialEntries: [`/agents/${key}`] },
  );
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <StrictMode>
        <RouterProvider router={router} />
      </StrictMode>
    </QueryClientProvider>,
  );
  return { ...rendered, router, queryClient };
}

describe('AgentEditorPage（/agents/:key 编辑器）', () => {
  it('已有新鲜查询缓存时也初始化草稿，不停留在加载定义中', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000 } },
    });
    queryClient.setQueryData(['agents', 'cs-agent'], { ...agents[0] });
    renderPage('cs-agent', queryClient);

    expect(await screen.findByText('Agent 定义编辑器 — cs-agent')).toBeInTheDocument();
    expect(screen.getByDisplayValue('智能客服')).toBeInTheDocument();
    expect(screen.queryByText(/加载定义中/)).not.toBeInTheDocument();
  });

  it('去调试台后立即返回，多次导航仍使用缓存加载完整编辑器', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000 } },
    });
    const { router } = renderPage('cs-agent', queryClient);
    const user = userEvent.setup();
    await screen.findByText('Agent 定义编辑器 — cs-agent');

    for (let index = 0; index < 3; index += 1) {
      await user.click(screen.getByRole('link', { name: '去调试台' }));
      await screen.findByText('agent-debug-stub');
      await act(async () => router.navigate(-1));
      expect(await screen.findByText('Agent 定义编辑器 — cs-agent')).toBeInTheDocument();
      expect(screen.getByDisplayValue('智能客服')).toBeInTheDocument();
      expect(screen.queryByText(/加载定义中/)).not.toBeInTheDocument();
    }
  });

  it('切换到另一个已缓存的 Agent 时清掉旧草稿和历史快照', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000 } },
    });
    for (const agent of agents) {
      queryClient.setQueryData(['agents', agent.key], { ...agent });
    }
    const { router } = renderPage('cs-agent', queryClient);
    const user = userEvent.setup();
    const nameInput = await screen.findByDisplayValue('智能客服');
    await user.clear(nameInput);
    await user.type(nameInput, '未保存的客服草稿');
    const sidebar = screen.getByText('版本历史').parentElement!.parentElement!;
    await user.click(within(sidebar).getByText('v1'));
    await screen.findByText('v1 只读快照');

    await act(async () => router.navigate('/agents/code-helper'));
    expect(await screen.findByText('Agent 定义编辑器 — code-helper')).toBeInTheDocument();
    expect(screen.getByDisplayValue('代码助手')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('未保存的客服草稿')).not.toBeInTheDocument();
    expect(screen.queryByText('v1 只读快照')).not.toBeInTheDocument();
  });

  it('后台重新获取定义不会覆盖当前未保存的编辑草稿', async () => {
    const { queryClient } = renderPage();
    const user = userEvent.setup();
    const nameInput = await screen.findByDisplayValue('智能客服');
    await user.clear(nameInput);
    await user.type(nameInput, '本地未保存草稿');
    server.use(
      http.get('*/v1/agents/cs-agent', () =>
        HttpResponse.json({ ...agents[0], name: '后台最新名称' }),
      ),
    );
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ['agents', 'cs-agent'], exact: true });
    });

    expect(screen.getByDisplayValue('本地未保存草稿')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('后台最新名称')).not.toBeInTheDocument();
  });

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

describe('Agent 编辑器 — 可选虚拟形象', () => {
  it('旧定义默认显示；关闭后随新版本保存，保留形象预览和文件名', async () => {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.get('*/v1/agents/cs-agent', () =>
        HttpResponse.json({ ...agents[0], show_avatar: undefined }),
      ),
      http.post('*/v1/agents/cs-agent/versions', async ({ request }) => {
        const definition = (await request.json()) as Record<string, unknown>;
        bodies.push(definition);
        return HttpResponse.json(
          { agent_key: 'cs-agent', version: definition.version, status: 'draft', definition },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderPage();
    const toggle = await screen.findByRole('switch', { name: '显示虚拟形象' });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    const card = screen.getByTestId('avatar-card');
    expect(await within(card).findByTestId('agent-avatar-img')).toBeInTheDocument();
    expect(within(card).getByText('avatar.svg')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /保存为新版本/ }));
    await screen.findByText(/已保存版本 v4/);
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({ show_avatar: false, avatar: 'avatar.svg', version: 4 });
    expect(screen.getByRole('switch', { name: '显示虚拟形象' })).not.toBeChecked();
  });

  it('重新进入编辑器回填已保存的关闭设置，也能重新开启', async () => {
    const definition = { ...agents[0], show_avatar: false };
    server.use(http.get('*/v1/agents/cs-agent', () => HttpResponse.json(definition)));
    const user = userEvent.setup();
    const page = renderPage();
    const toggle = await screen.findByRole('switch', { name: '显示虚拟形象' });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    expect(toggle).toBeChecked();
    page.unmount();
    renderPage();
    expect(await screen.findByRole('switch', { name: '显示虚拟形象' })).not.toBeChecked();
  });

  it('上传形象产生新版本，不覆盖尚未保存的显示开关', async () => {
    const originalFetch = globalThis.fetch;
    const uploads: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT' && /\/v1\/agents\/cs-agent\/avatar$/.test(String(input))) {
        uploads.push(String(input));
        return new Response(
          JSON.stringify({ ...agents[0], version: 4, avatar: 'avatar.png', show_avatar: true }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        );
      }
      return originalFetch(input, init);
    }) as typeof fetch;
    try {
      const user = userEvent.setup();
      renderPage();
      await user.click(await screen.findByRole('switch', { name: '显示虚拟形象' }));
      await user.upload(
        screen.getByLabelText('形象文件'),
        new File(['png'], 'avatar.png', { type: 'image/png' }),
      );
      await user.click(screen.getByRole('button', { name: '上传' }));
      await waitFor(() => expect(uploads).toHaveLength(1));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /保存为新版本/ })).toHaveTextContent('v5'),
      );
      expect(screen.getByRole('switch', { name: '显示虚拟形象' })).not.toBeChecked();
      expect(within(screen.getByTestId('avatar-card')).getByText('avatar.png')).toBeInTheDocument();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('只读表单不可改变显示设置，历史快照只展示已保存的值', async () => {
    const onChange = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const form = render(
      <QueryClientProvider client={queryClient}>
        <AgentForm draft={{ ...agents[0], show_avatar: false }} readOnly onChange={onChange} />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const toggle = screen.getByRole('switch', { name: '显示虚拟形象' });
    expect(toggle).toBeDisabled();
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    expect(onChange).not.toHaveBeenCalled();
    form.unmount();

    server.use(
      http.get('*/v1/agents/cs-agent/versions/1', () =>
        HttpResponse.json({
          agent_key: 'cs-agent',
          version: 1,
          status: 'draft',
          definition: { ...agents[0], version: 1, show_avatar: false },
        }),
      ),
    );
    renderPage();
    await screen.findByText('Agent 定义编辑器 — cs-agent');
    const sidebar = screen.getByText('版本历史').parentElement!.parentElement!;
    await user.click(within(sidebar).getByText('v1'));
    expect(await within(sidebar).findByText(/"show_avatar": false/)).toBeInTheDocument();
    expect(within(sidebar).queryByRole('switch')).not.toBeInTheDocument();
  });
});

describe('Agent 编辑器 — 动态大脑槽位', () => {
  const slots: BrainProfileOut[] = [
    {
      key: 'main_reasoning',
      name: '主力推理',
      description: '',
      aliases: ['smart'],
      reference_count: 1,
      agent_count: 1,
    },
    {
      key: 'customer_reasoning',
      name: '客服推理',
      description: '',
      aliases: [],
      reference_count: 0,
      agent_count: 0,
    },
    {
      key: 'low_cost',
      name: '低成本',
      description: '',
      aliases: ['cheap'],
      reference_count: 0,
      agent_count: 0,
    },
  ];

  function DynamicForm({ initial }: { initial: AgentDefinition }) {
    const [draft, setDraft] = useState(normalizeDefinition(initial));
    return (
      <>
        <AgentForm draft={draft} onChange={setDraft} />
        <pre data-testid="dynamic-brain">{JSON.stringify(draft.brain)}</pre>
      </>
    );
  }

  function renderDynamic(initial: AgentDefinition) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(['brain-profiles'], { env: 'dev', items: slots });
    server.use(
      http.get('*/v1/brain-profiles', () => HttpResponse.json({ env: 'dev', items: slots })),
    );
    return render(
      <QueryClientProvider client={queryClient}>
        <DynamicForm initial={initial} />
      </QueryClientProvider>,
    );
  }

  it('自定义槽位出现在主槽位和降级选择中，旧标识可读且不自动改写草稿', async () => {
    Element.prototype.hasPointerCapture = () => false;
    Element.prototype.setPointerCapture = () => undefined;
    Element.prototype.releasePointerCapture = () => undefined;
    renderDynamic({
      ...agents[0],
      brain: { profile: 'smart', allow_run_override: false, fallback_profiles: ['cheap'] },
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: '大脑' }));
    expect(screen.getByRole('combobox', { name: '逻辑大脑槽位' })).toHaveTextContent(
      '主力推理（旧标识 smart → main_reasoning）',
    );
    expect(screen.getByTestId('dynamic-brain')).toHaveTextContent('"profile":"smart"');
    expect(screen.getByRole('checkbox', { name: '低成本（low_cost）' })).toBeChecked();
    // 同一个槽位的 canonical 与 alias 不重复显示降级项。
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    await user.click(screen.getByRole('checkbox', { name: '客服推理（customer_reasoning）' }));
    expect(screen.getByTestId('dynamic-brain')).toHaveTextContent(
      '"fallback_profiles":["cheap","customer_reasoning"]',
    );
    await user.click(screen.getByRole('combobox', { name: '逻辑大脑槽位' }));
    await user.click(await screen.findByRole('option', { name: '客服推理（customer_reasoning）' }));
    expect(screen.getByTestId('dynamic-brain')).toHaveTextContent('"profile":"customer_reasoning"');
    expect(screen.getByTestId('dynamic-brain')).toHaveTextContent('"fallback_profiles":["cheap"]');
  });

  it('新建 Agent 使用动态目录；smart 改名后默认选 canonical 标识', async () => {
    const bodies: AgentDefinition[] = [];
    server.use(
      http.get('*/v1/brain-profiles', () => HttpResponse.json({ env: 'dev', items: slots })),
      http.post('*/v1/agents', async ({ request }) => {
        const body = (await request.json()) as AgentDefinition;
        bodies.push(body);
        return HttpResponse.json(body, { status: 201 });
      }),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter(
      [
        { path: '/', element: <CreateAgentDialog open onOpenChange={() => undefined} /> },
        { path: '/agents/:key', element: <div>created-custom-agent</div> },
      ],
      { initialEntries: ['/'] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: '逻辑大脑槽位' })).toHaveTextContent(
        '主力推理（main_reasoning）',
      ),
    );
    await user.type(screen.getByLabelText('Key（唯一，创建后不可改）'), 'dynamic-agent');
    await user.type(screen.getByLabelText('名称'), '动态助手');
    await user.click(screen.getByRole('button', { name: '创建' }));
    await screen.findByText('created-custom-agent');
    expect(bodies[0].brain?.profile).toBe('main_reasoning');
  });

  it('目录为空时不能提交不存在的默认槽位', async () => {
    server.use(http.get('*/v1/brain-profiles', () => HttpResponse.json({ env: 'dev', items: [] })));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([
      { path: '/', element: <CreateAgentDialog open onOpenChange={() => undefined} /> },
    ]);
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await screen.findByText('请先在模型中心新增槽位');
    expect(screen.getByRole('button', { name: '创建' })).toBeDisabled();
  });
});
