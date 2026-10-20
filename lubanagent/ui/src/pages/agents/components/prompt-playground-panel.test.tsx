/**
 * PromptPlaygroundPanel（调试台「Prompt 试跑」Tab 面板）：A/B 并排试跑 + session_id 约定。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it } from 'vitest';

import type { AgentDefinition } from '@/api/hooks';
import { server } from '@/mocks/node';

import { PromptPlaygroundPanel } from './prompt-playground-panel';

beforeAll(() => {
  // jsdom 缺 Pointer Capture / ResizeObserver（Radix Select 依赖）
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  if (globalThis.ResizeObserver === undefined) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

/** 与 mocks/fixtures.ts 的 cs-agent 同形（面板是纯 props 组件，直接内联定义） */
const CS_AGENT = {
  key: 'cs-agent',
  name: '智能客服',
  description: '内置验证场景：订单查询 + 退款审批',
  role: '客服专员',
  greeting: '你好，我是 LubanAgent。请问有什么可以帮你？',
  instructions: '你是 {company} 的客服专员，先安抚再解决，退款必须走审批。',
  prompt_variables: { company: 'Luban 商店' },
  status: 'published',
  version: 3,
} as unknown as AgentDefinition;

/** 捕获 POST /v1/agents/:key/runs 请求体（clone 读 json，不打扰流） */
function captureRunBodies(): {
  bodies: { session_id?: string | null; variables?: Record<string, string>; instructions_override?: string }[];
} {
  const bodies: {
    session_id?: string | null;
    variables?: Record<string, string>;
    instructions_override?: string;
  }[] = [];
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'POST' && /\/v1\/agents\/.+\/runs$/.test(request.url)) {
      bodies.push(await request.clone().json());
    }
  });
  return { bodies };
}

function renderPanel(agent: AgentDefinition = CS_AGENT) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PromptPlaygroundPanel agent={agent} />
    </QueryClientProvider>,
  );
}

describe('Prompt 试跑面板（调试台 Tab）', () => {
  it('载入当前 Agent 定义：instructions 双面板 + variables，修改 variable 后并排试跑', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPanel();

    // 原定义只读展示；修改后面板为可编辑副本（Tab 载入即播种，无需选 Agent）
    expect((await screen.findAllByText(/你是 \{company\} 的客服专员/)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('修改后 instructions')).toHaveValue(
      '你是 {company} 的客服专员，先安抚再解决，退款必须走审批。',
    );
    const valueInput = screen.getByLabelText('变量值 1');
    expect(valueInput).toHaveValue('Luban 商店');

    // 修改 variable（请求体原生字段，即时生效）
    await user.clear(valueInput);
    await user.type(valueInput, '测试商店');

    await user.type(screen.getByLabelText('试跑输入（两侧共用）'), '帮我查订单');
    await user.click(screen.getByRole('button', { name: /并排试跑/ }));

    const originalPanel = screen.getByTestId('panel-original');
    const modifiedPanel = screen.getByTestId('panel-modified');
    expect(
      await within(originalPanel).findByText(/订单 12345 已到达杭州转运中心/, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(
      await within(modifiedPanel).findByText(/订单 12345 已到达杭州转运中心/, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(await within(originalPanel).findByTestId('run-summary', {}, { timeout: 5000 }));
    expect(await within(modifiedPanel).findByTestId('run-summary', {}, { timeout: 5000 }));
  });

  it('并排试跑两条请求共享同一 session_id，两次点击重新生成', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPanel();

    await user.type(screen.getByLabelText('试跑输入（两侧共用）'), '帮我查订单');
    await user.click(screen.getByRole('button', { name: /并排试跑/ }));
    await waitForBodies(bodies, 2);
    await screen.findAllByTestId('run-summary', {}, { timeout: 5000 });

    // original / modified 同一次对比共享同一 session_id（Trace 列表成对可查）
    expect(bodies[0].session_id).toBeTruthy();
    expect(bodies[1].session_id).toBe(bodies[0].session_id);

    // 第二次点击 → 新 session_id（避免会话记忆跨轮污染对比）
    await user.click(await screen.findByRole('button', { name: /并排试跑/ }, { timeout: 5000 }));
    await waitForBodies(bodies, 4);
    expect(bodies[2].session_id).toBeTruthy();
    expect(bodies[3].session_id).toBe(bodies[2].session_id);
    expect(bodies[2].session_id).not.toBe(bodies[0].session_id);
  });

  it('改 instructions 经 instructions_override 携带；未改时不携带', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPanel();

    // 未改 instructions → modified 请求不携带 instructions_override（variables 仍即时生效）
    await user.type(screen.getByLabelText('试跑输入（两侧共用）'), '你好');
    await user.click(screen.getByRole('button', { name: /并排试跑/ }));
    await waitForBodies(bodies, 2);
    await screen.findAllByTestId('run-summary', {}, { timeout: 5000 });
    expect(bodies[1].instructions_override).toBeUndefined();

    // 改 instructions → 携带临时替换
    const editor = screen.getByLabelText('修改后 instructions');
    await user.clear(editor);
    await user.type(editor, '你是新版客服。');
    await user.click(await screen.findByRole('button', { name: /并排试跑/ }, { timeout: 5000 }));
    await waitForBodies(bodies, 4);
    expect(bodies[3].instructions_override).toBe('你是新版客服。');
  });
});

async function waitForBodies(
  bodies: unknown[],
  count: number,
  attempts = 40,
): Promise<void> {
  for (let i = 0; i < count * attempts; i++) {
    if (bodies.length >= count) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  expect(bodies.length).toBeGreaterThanOrEqual(count);
}
