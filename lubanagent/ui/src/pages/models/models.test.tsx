import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeAll, describe, expect, it } from 'vitest';

import { server } from '@/mocks/node';
import { brainMappings, brainProfiles, viewerUser } from '@/mocks/fixtures';
import type { BrainProfileOut } from '@/api/hooks';

import ModelsPage from './ModelsPage';

beforeAll(() => {
  // radix Select 在 jsdom 需要的指针捕获/滚动 polyfill
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ModelsPage />
    </QueryClientProvider>,
  );
}

describe('模型中心', () => {
  it('模型表渲染：key/provider/能力徽章/启用状态', async () => {
    renderPage();
    // key 与 model_name 两列同名，用 All 变体
    expect((await screen.findAllByText('deepseek-chat')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('qwen-plus').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('glm-4v').length).toBeGreaterThanOrEqual(1);
    // 能力徽章（deepseek-chat / qwen-plus 均有 tool_calling，glm-4v 有 vision）
    expect(screen.getAllByText('tool_calling').length).toBeGreaterThanOrEqual(2);
    // vision 同时出现于能力徽章与大脑槽位标签
    expect(screen.getAllByText('vision').length).toBeGreaterThanOrEqual(2);
    // glm-4v 停用，其余启用
    expect(screen.getByText('停用')).toBeInTheDocument();
    expect(screen.getAllByText('启用').length).toBeGreaterThanOrEqual(2);
    // 大脑映射卡四槽位
    for (const profile of ['fast', 'smart', 'cheap', 'vision']) {
      expect(screen.getByRole('combobox', { name: `大脑槽位 ${profile}` })).toBeInTheDocument();
    }
  });

  it('编辑历史模型不回填密钥遮蔽值且未提供旧 base_url', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    server.use(
      http.get('*/v1/models', () =>
        HttpResponse.json([
          {
            key: 'legacy-secret',
            provider: 'openai_compat',
            model_name: 'old',
            factory: 'openai_compat',
            api_key_env: 'SYNTHETIC_KEY',
            enabled: true,
            capability: {},
            params: { temperature: 0.3, api_key: '[REDACTED]' },
          },
        ]),
      ),
      http.put('*/v1/models', async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ key: 'legacy-secret' });
      }),
    );
    renderPage();
    await screen.findByText('legacy-secret');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '编辑' }));
    await user.click(screen.getByRole('button', { name: '保存' }));
    expect(bodies).toHaveLength(1);
    expect(bodies[0].base_url).toBeNull();
    expect(bodies[0].params).toEqual({ temperature: 0.3 });
    expect(JSON.stringify(bodies[0])).not.toContain('[REDACTED]');
  });

  it('大脑映射保存：选模型 → PUT /v1/brain-mappings', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    server.use(
      http.put('*/v1/brain-mappings', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        bodies.push(body);
        return HttpResponse.json({
          env: 'dev',
          profile: body.profile,
          model_key: body.model_key,
        });
      }),
    );

    renderPage();
    await screen.findAllByText('deepseek-chat');

    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '大脑槽位 smart' }));
    await user.click(await screen.findByRole('option', { name: 'deepseek-chat' }));
    await user.click(screen.getByRole('button', { name: '保存 smart 映射' }));

    expect(await screen.findByText('已保存')).toBeInTheDocument();
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({ env: 'dev', profile: 'smart', model_key: 'deepseek-chat' });
  });

  it('回填服务端环境及已有映射，不把生产配置写进 dev', async () => {
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.get('*/v1/brain-profiles', () =>
        HttpResponse.json({ env: 'prod', items: brainProfiles }),
      ),
      http.get('*/v1/brain-mappings', ({ request }) => {
        expect(new URL(request.url).searchParams.get('env')).toBe('prod');
        return HttpResponse.json([{ env: 'prod', profile: 'smart', model_key: 'qwen-plus' }]);
      }),
      http.put('*/v1/brain-mappings', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        bodies.push(body);
        return HttpResponse.json(body);
      }),
    );
    renderPage();
    expect(await screen.findByText('env: prod')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: '大脑槽位 smart' })).toHaveTextContent(
        'qwen-plus',
      ),
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: '大脑槽位 smart' }));
    await user.click(await screen.findByRole('option', { name: 'deepseek-chat' }));
    await user.click(screen.getByRole('button', { name: '保存 smart 映射' }));
    await screen.findByText('已保存');
    expect(bodies[0]).toEqual({ env: 'prod', profile: 'smart', model_key: 'deepseek-chat' });
  });

  it('新增槽位并映射模型，保存后目录刷新且新槽位可编辑', async () => {
    let items = structuredClone(brainProfiles);
    let mappings = structuredClone(brainMappings);
    const bodies: Record<string, unknown>[] = [];
    server.use(
      http.get('*/v1/brain-profiles', () => HttpResponse.json({ env: 'dev', items })),
      http.get('*/v1/brain-mappings', () => HttpResponse.json(mappings)),
      http.post('*/v1/brain-profiles', async ({ request }) => {
        const body = (await request.json()) as Record<string, string>;
        bodies.push(body);
        const slot: BrainProfileOut = {
          key: body.key,
          name: body.name,
          description: body.description,
          aliases: [],
          reference_count: 0,
          agent_count: 0,
        };
        items = [...items, slot];
        mappings = [...mappings, { env: body.env, profile: body.key, model_key: body.model_key }];
        return HttpResponse.json(slot, { status: 201 });
      }),
    );
    renderPage();
    await screen.findByRole('combobox', { name: '大脑槽位 smart' });
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByRole('button', { name: '新增槽位' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: '新增槽位' }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText('槽位名称'), '客服推理');
    await user.type(within(dialog).getByLabelText('槽位标识'), 'customer_reasoning');
    await user.type(within(dialog).getByLabelText('用途说明'), '处理复杂客服问题');
    await user.click(within(dialog).getByRole('combobox', { name: '槽位映射模型' }));
    await user.click(await screen.findByRole('option', { name: 'deepseek-chat' }));
    await user.click(within(dialog).getByRole('button', { name: '保存槽位' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(
      await screen.findByRole('combobox', { name: '大脑槽位 customer_reasoning' }),
    ).toHaveTextContent('deepseek-chat');
    expect(screen.getByRole('button', { name: '编辑槽位 customer_reasoning' })).toBeEnabled();
    expect(bodies[0]).toMatchObject({
      key: 'customer_reasoning',
      name: '客服推理',
      description: '处理复杂客服问题',
      env: 'dev',
      model_key: 'deepseek-chat',
    });
  });

  it('编辑预设名称、标识及用途，改名后回填映射和旧别名', async () => {
    let slot: BrainProfileOut = { ...brainProfiles[1] };
    const patches: Record<string, unknown>[] = [];
    server.use(
      http.get('*/v1/brain-profiles', () => HttpResponse.json({ env: 'dev', items: [slot] })),
      http.get('*/v1/brain-mappings', () =>
        HttpResponse.json([{ env: 'dev', profile: slot.key, model_key: 'deepseek-chat' }]),
      ),
      http.patch('*/v1/brain-profiles/smart', async ({ request }) => {
        const patch = (await request.json()) as Record<string, string>;
        patches.push(patch);
        slot = {
          ...slot,
          key: patch.key,
          name: patch.name,
          description: patch.description,
          aliases: ['smart'],
        };
        return HttpResponse.json(slot);
      }),
    );
    renderPage();
    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '编辑槽位 smart' })).toBeEnabled(),
    );
    await user.click(screen.getByRole('button', { name: '编辑槽位 smart' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByLabelText('槽位名称')).toHaveValue('smart');
    expect(within(dialog).getByRole('combobox', { name: '槽位映射模型' })).toHaveTextContent(
      'deepseek-chat',
    );
    await user.clear(within(dialog).getByLabelText('槽位名称'));
    await user.type(within(dialog).getByLabelText('槽位名称'), '主力推理');
    await user.clear(within(dialog).getByLabelText('槽位标识'));
    await user.type(within(dialog).getByLabelText('槽位标识'), 'main_reasoning');
    await user.clear(within(dialog).getByLabelText('用途说明'));
    await user.type(within(dialog).getByLabelText('用途说明'), '复杂推理任务');
    await user.click(within(dialog).getByRole('button', { name: '保存槽位' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(
      await screen.findByRole('combobox', { name: '大脑槽位 main_reasoning' }),
    ).toHaveTextContent('deepseek-chat');
    expect(screen.getByText('兼容旧标识：smart')).toBeInTheDocument();
    expect(patches[0]).toMatchObject({
      key: 'main_reasoning',
      name: '主力推理',
      description: '复杂推理任务',
    });
    expect(patches[0]).not.toHaveProperty('model_key');
  });

  it('使用中不允许删除，显示 Agent、版本、主/降级字段且不发 DELETE', async () => {
    const deletes: string[] = [];
    server.use(
      http.get('*/v1/brain-profiles/smart/references', () =>
        HttpResponse.json({
          key: 'smart',
          agent_count: 1,
          references: [
            {
              agent_key: 'cs-agent',
              agent_name: '客服',
              version: 3,
              current: true,
              field: 'brain.profile',
            },
            {
              agent_key: 'cs-agent',
              agent_name: '客服',
              version: 1,
              current: false,
              field: 'brain.fallback_profiles',
            },
          ],
        }),
      ),
      http.delete('*/v1/brain-profiles/:key', ({ params }) => {
        deletes.push(String(params.key));
        return HttpResponse.json({ removed: true });
      }),
    );
    renderPage();
    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '删除槽位 smart' })).toBeEnabled(),
    );
    await user.click(screen.getByRole('button', { name: '删除槽位 smart' }));
    expect(await screen.findByText('槽位正在使用，不能删除')).toBeInTheDocument();
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('主槽位')).toBeInTheDocument();
    expect(within(dialog).getByText('降级槽位')).toBeInTheDocument();
    expect(within(dialog).getByText('历史版本')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '确认删除槽位' })).toBeDisabled();
    expect(deletes).toEqual([]);
  });

  it('未使用槽位可以删除，删除成功后目录刷新', async () => {
    let items = structuredClone(brainProfiles);
    server.use(
      http.get('*/v1/brain-profiles', () => HttpResponse.json({ env: 'dev', items })),
      http.delete('*/v1/brain-profiles/vision', () => {
        items = items.filter((slot) => slot.key !== 'vision');
        return HttpResponse.json({ removed: true });
      }),
    );
    renderPage();
    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '删除槽位 vision' })).toBeEnabled(),
    );
    await user.click(screen.getByRole('button', { name: '删除槽位 vision' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '确认删除槽位' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: '确认删除槽位' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByRole('combobox', { name: '大脑槽位 vision' })).not.toBeInTheDocument();
  });

  it('预检之后新增了使用者时，显示 DELETE 409 的引用并保留槽位', async () => {
    server.use(
      http.get('*/v1/brain-profiles/vision/references', () =>
        HttpResponse.json({ key: 'vision', agent_count: 0, references: [] }),
      ),
      http.delete('*/v1/brain-profiles/vision', () =>
        HttpResponse.json(
          {
            error: {
              code: 'BRAIN_PROFILE_IN_USE',
              message: '槽位正被 Agent 使用，不能删除',
              details: {
                references: [
                  {
                    agent_key: 'new-agent',
                    agent_name: '刚创建的助手',
                    version: 1,
                    current: true,
                    field: 'brain.profile',
                  },
                ],
              },
            },
          },
          { status: 409 },
        ),
      ),
    );
    renderPage();
    const user = userEvent.setup();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '删除槽位 vision' })).toBeEnabled(),
    );
    await user.click(screen.getByRole('button', { name: '删除槽位 vision' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '确认删除槽位' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: '确认删除槽位' }));
    expect(await screen.findByText('刚创建的助手')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认删除槽位' })).toBeDisabled();
    expect(screen.getByRole('dialog')).toHaveTextContent('槽位正被 Agent 使用，不能删除');
  });

  it('只读角色可以查看回填值，但新增、编辑、删除和保存均禁用', async () => {
    server.use(http.get('*/v1/auth/me', () => HttpResponse.json(viewerUser)));
    renderPage();
    await screen.findByText('当前角色只有查看权限，修改需要 config:write。');
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: '大脑槽位 smart' })).toHaveTextContent(
        'deepseek-chat',
      ),
    );
    for (const name of ['新增槽位', '编辑槽位 smart', '删除槽位 smart', '保存 smart 映射']) {
      expect(screen.getByRole('button', { name })).toBeDisabled();
    }
    expect(screen.getByRole('combobox', { name: '大脑槽位 smart' })).toBeDisabled();
  });
});
