import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeAll, describe, expect, it } from 'vitest';

import { server } from '@/mocks/node';

import AgentDebugPage from './AgentDebugPage';

/** 捕获 POST /v1/agents/:key/runs 请求体（clone 读 json，不打扰流） */
function captureRunBodies(): {
  bodies: {
    session_id?: string | null;
    variables?: Record<string, string>;
    instructions_override?: string;
  }[];
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

beforeAll(() => {
  // jsdom 未实现 scrollIntoView（调试台自动滚动到底）
  Element.prototype.scrollIntoView = () => {};
  // jsdom 缺 Pointer Capture / ResizeObserver（试跑 Tab 的 Radix Select 依赖）
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  if (globalThis.ResizeObserver === undefined) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/agents/cs-agent/debug']}>
        <Routes>
          <Route path="/agents/:key/debug" element={<AgentDebugPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('运行调试台', () => {
  it('SSE 流渲染：token 累积、tool 条目、run.completed 汇总卡', async () => {
    const user = userEvent.setup();
    renderPage();

    // 空态问候语（来自 Agent 定义）
    expect(await screen.findByText(/你好，我是 LubanAgent/)).toBeInTheDocument();

    await user.type(screen.getByLabelText('调试输入'), '我的订单到哪了');
    await user.click(screen.getByRole('button', { name: '发送' }));

    // 进行中显示取消按钮
    expect(await screen.findByRole('button', { name: '取消' })).toBeInTheDocument();

    // token.delta 打字机累积（多段拼接在同一气泡）
    expect(
      await screen.findByText(/好的，我先查一下订单。/, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/订单 12345 已到达杭州转运中心/, {}, { timeout: 3000 }),
    ).toBeInTheDocument();

    // tool.started/finished 条目（工具名 + 完成耗时）
    expect(screen.getByText('query-order')).toBeInTheDocument();
    expect(screen.getByText(/完成 · /)).toBeInTheDocument();

    // run.completed 汇总卡：实际模型 / token / 费用 / 结束原因（取 mock Run 记录）
    const summary = await screen.findByTestId('run-summary', {}, { timeout: 3000 });
    expect(await screen.findByText('deepseek-chat')).toBeInTheDocument();
    expect(summary).toHaveTextContent('512');
    expect(summary).toHaveTextContent('128');
    expect(summary).toHaveTextContent('final_answer');

    // 流结束后回到发送态
    expect(await screen.findByRole('button', { name: '发送' })).toBeInTheDocument();
  });

  it('多轮对话：新一轮归档上一轮，历史保留在页面内', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText('调试输入'), '第一轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByTestId('run-summary', {}, { timeout: 3000 });
    // 等流完全关闭、回到发送态
    await screen.findByRole('button', { name: '发送' });

    await user.type(screen.getByLabelText('调试输入'), '第二轮');
    await user.click(screen.getByRole('button', { name: '发送' }));

    // 两轮用户输入都在页面上，各出一个汇总卡
    expect(await screen.findByText('第二轮')).toBeInTheDocument();
    expect(screen.getByText('第一轮')).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId('run-summary').length).toBe(2), {
      timeout: 3000,
    });
  });

  it('调试多轮共用同一 session_id（Trace 列表按会话归组）', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText('调试输入'), '第一轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await user.type(screen.getByLabelText('调试输入'), '第二轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await waitFor(() => expect(bodies.length).toBe(2), { timeout: 3000 });
    expect(bodies[0].session_id).toBeTruthy();
    expect(bodies[1].session_id).toBe(bodies[0].session_id);
  });

  it('Tab 切换到 Prompt 试跑：载入当前 Agent 定义，改 variables 即时生效并排试跑', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();

    await user.click(await screen.findByRole('tab', { name: 'Prompt 试跑' }));

    // Tab 载入即播种当前 Agent 定义（无需选 Agent）
    const editor = await screen.findByLabelText('修改后 instructions');
    expect(editor).toHaveValue('你是 {company} 的客服专员，先安抚再解决，退款必须走审批。');
    const valueInput = screen.getByLabelText('变量值 1');
    expect(valueInput).toHaveValue('Luban 商店');

    // 改 variable → 并排试跑：original 用库内变量、modified 用编辑值；未改 instructions 不带 override
    await user.clear(valueInput);
    await user.type(valueInput, '测试商店');
    await user.type(screen.getByLabelText('试跑输入（两侧共用）'), '帮我查订单');
    await user.click(screen.getByRole('button', { name: /并排试跑/ }));

    await screen.findAllByTestId('run-summary', {}, { timeout: 5000 });
    await waitFor(() => expect(bodies.length).toBe(2), { timeout: 3000 });
    expect(bodies[0].variables).toEqual({ company: 'Luban 商店' });
    expect(bodies[1].variables).toEqual({ company: '测试商店' });
    expect(bodies[1].instructions_override).toBeUndefined();
  });

  it('Tab 切换不丢状态（forceMount）：对话轮次与试跑编辑态切换后保留', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();

    // 对话 Tab 发一轮
    await user.type(await screen.findByLabelText('调试输入'), '第一轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByTestId('run-summary', {}, { timeout: 3000 });
    await screen.findByRole('button', { name: '发送' });

    // 切到试跑 Tab 输入标记文本
    await user.click(screen.getByRole('tab', { name: 'Prompt 试跑' }));
    const editor = await screen.findByLabelText('修改后 instructions');
    await user.clear(editor);
    await user.type(editor, '切换保留标记');

    // 切回对话 Tab：第一轮仍在；再切回试跑：标记仍在
    await user.click(screen.getByRole('tab', { name: '对话' }));
    expect(screen.getByText('第一轮')).toBeInTheDocument();
    expect(screen.getAllByTestId('run-summary').length).toBe(1);

    await user.click(screen.getByRole('tab', { name: 'Prompt 试跑' }));
    expect(screen.getByLabelText('修改后 instructions')).toHaveValue('切换保留标记');
  });

  it('对话与试跑的 session_id 相互独立（连续会话 vs 无状态对比）', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();

    // 对话一条
    await user.type(await screen.findByLabelText('调试输入'), '你好');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    // 试跑一对
    await user.click(screen.getByRole('tab', { name: 'Prompt 试跑' }));
    await user.type(screen.getByLabelText('试跑输入（两侧共用）'), '查订单');
    await user.click(screen.getByRole('button', { name: /并排试跑/ }));
    await screen.findAllByTestId('run-summary', {}, { timeout: 5000 });

    await waitFor(() => expect(bodies.length).toBe(3), { timeout: 3000 });
    // 对话 session 与试跑 session 不同；试跑两条共享
    expect(bodies[0].session_id).not.toBe(bodies[1].session_id);
    expect(bodies[1].session_id).toBe(bodies[2].session_id);
  });
});
