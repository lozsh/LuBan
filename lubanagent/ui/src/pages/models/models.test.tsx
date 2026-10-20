import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeAll, describe, expect, it } from 'vitest';

import { server } from '@/mocks/node';

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
});
