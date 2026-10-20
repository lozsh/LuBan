import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeAll, describe, expect, it } from 'vitest';

import { server } from '@/mocks/node';

import ChatPage from './ChatPage';

interface CapturedBody {
  url: string;
  body: {
    input?: string;
    session_id?: string;
    user_id?: string;
    history?: Array<{ role: string; content: string }>;
  };
}

/** 捕获 POST /v1/agents/:key/runs 请求体（clone 读 json，不打扰流） */
function captureRunBodies(): { bodies: CapturedBody[] } {
  const bodies: CapturedBody[] = [];
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'POST' && /\/v1\/agents\/.+\/runs$/.test(request.url)) {
      bodies.push({
        url: request.url,
        body: (await request.clone().json()) as CapturedBody['body'],
      });
    }
  });
  return { bodies };
}

function captureDecideBodies(): { bodies: Array<{ id: string; body: Record<string, unknown> }> } {
  const bodies: Array<{ id: string; body: Record<string, unknown> }> = [];
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'POST' && /\/v1\/approvals\/.+\/decide$/.test(request.url)) {
      const id = request.url.split('/').slice(-2, -1)[0];
      bodies.push({ id, body: (await request.clone().json()) as Record<string, unknown> });
    }
  });
  return { bodies };
}

beforeAll(() => {
  // jsdom 未实现 scrollIntoView（聊天页自动滚动到底）
  Element.prototype.scrollIntoView = () => {};
  // jsdom 缺 Pointer Capture / ResizeObserver（Agent 下拉 Radix Select 依赖）
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
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 覆盖 runs handler：发审批请求帧后直接关流（无终态帧 → 审批卡常驻可交互） */
function overrideRunsWithApproval(): void {
  server.use(
    http.post('*/v1/agents/:key/runs', () => {
      const encoder = new TextEncoder();
      const frames = [
        'event: run.started\ndata: {"run_id":"run-appr","type":"run_started"}\n\n',
        'event: token.delta\ndata: {"run_id":"run-appr","type":"model_token_delta","text":"这笔退款金额较大"}\n\n',
        'event: tool.approval.requested\ndata: {"run_id":"run-appr","type":"tool_approval_requested","approval_id":"appr-001","tool":"refund-apply","args":{"order_id":"ORD-1001","amount":800}}\n\n',
      ];
      const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
          for (const frame of frames) {
            controller.enqueue(encoder.encode(frame));
            await new Promise((resolve) => setTimeout(resolve, 40));
          }
          controller.close();
        },
      });
      return new HttpResponse(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    }),
    // decide 直接成功（不依赖 fixtures 里的审批记录）
    http.post('*/v1/approvals/:approvalId/decide', () => HttpResponse.json({ status: 'approved' })),
  );
}

describe('ChatPage（/chat 用户端聊天）', () => {
  it('发消息 → 流式回复 + 工具状态行；无任何调试件（用户端干净）', async () => {
    const user = userEvent.setup();
    renderPage();

    // 空会话：默认选中第一个 Agent，显示 greeting；左侧陪伴栏渲染形象（cs-agent 配了 avatar.svg）
    expect(await screen.findByText(/你好，我是 LubanAgent/)).toBeInTheDocument();
    // H13：头像经鉴权 fetch 转 blob URL（不再裸 src 直连 /v1）
    const avatarImg = await screen.findByTestId('agent-avatar-img');
    expect(avatarImg.getAttribute('src')).toMatch(/^blob:/);

    await user.type(screen.getByLabelText('聊天输入'), '我的订单到哪了');
    await user.click(screen.getByRole('button', { name: '发送' }));

    // 用户消息气泡
    expect(await screen.findByText('我的订单到哪了')).toBeInTheDocument();
    // 终态后 markdown 渲染 run.completed 的 output
    expect(
      await screen.findByText(/订单 12345 已到达杭州转运中心/, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    // 工具状态行：工具名 + 完成（无参数/结果折叠）
    expect(screen.getByTestId('chat-tool-row')).toHaveTextContent('query-order');
    expect(screen.getByTestId('chat-tool-row')).toHaveTextContent('完成');

    // 用户端干净：调试台组件（汇总卡/工具参数折叠）不存在
    expect(screen.queryByTestId('run-summary')).not.toBeInTheDocument();
    expect(screen.queryByTestId('tool-entry')).not.toBeInTheDocument();
  });

  it('多轮共用 session_id 且带 user_id=chat-user；history 前端拼接', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup();
    renderPage();

    // greeting 出现 = Agent 列表已加载、输入框解禁
    await screen.findByText(/你好，我是 LubanAgent/);
    await user.type(screen.getByLabelText('聊天输入'), '第一轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await user.type(screen.getByLabelText('聊天输入'), '第二轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await waitFor(() => expect(bodies.length).toBe(2), { timeout: 3000 });
    expect(bodies[0].body.session_id).toBeTruthy();
    expect(bodies[0].body.user_id).toBe('chat-user');
    expect(bodies[1].body.session_id).toBe(bodies[0].body.session_id);
    // 第二轮 history 携带第一轮 user/assistant 对
    expect(bodies[1].body.history?.[0]).toEqual({ role: 'user', content: '第一轮' });
    expect(bodies[1].body.history?.[1]?.role).toBe('assistant');
  });

  it('新会话：换 session_id、清轮次、greeting 回归', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup();
    renderPage();

    // greeting 出现 = Agent 列表已加载、输入框解禁
    await screen.findByText(/你好，我是 LubanAgent/);
    await user.type(screen.getByLabelText('聊天输入'), '第一轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await user.click(screen.getByRole('button', { name: /新会话/ }));

    // 旧轮次消失、greeting 回归
    expect(screen.queryByText('第一轮')).not.toBeInTheDocument();
    expect(await screen.findByText(/你好，我是 LubanAgent/)).toBeInTheDocument();

    await user.type(screen.getByLabelText('聊天输入'), '新话题');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await waitFor(() => expect(bodies.length).toBe(2), { timeout: 3000 });
    expect(bodies[1].body.session_id).not.toBe(bodies[0].body.session_id);
    expect(bodies[1].body.history).toEqual([]);
  });

  it('切换 Agent：请求打到新 Agent 且会话重置', async () => {
    const { bodies } = captureRunBodies();
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();

    // greeting 出现 = Agent 列表已加载、输入框解禁
    await screen.findByText(/你好，我是 LubanAgent/);
    await user.type(screen.getByLabelText('聊天输入'), '第一轮');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    // Select 切到 code-helper
    await user.click(screen.getByRole('combobox', { name: '选择 Agent' }));
    await user.click(await screen.findByRole('option', { name: /code-helper/ }));

    // code-helper 无 avatar → 头部回退聊天图标
    expect(screen.getByTestId('agent-avatar-fallback')).toBeInTheDocument();

    await user.type(screen.getByLabelText('聊天输入'), '写个函数');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByRole('button', { name: '发送' });

    await waitFor(() => expect(bodies.length).toBe(2), { timeout: 3000 });
    expect(bodies[0].url).toContain('/v1/agents/cs-agent/runs');
    expect(bodies[1].url).toContain('/v1/agents/code-helper/runs');
    expect(bodies[1].body.session_id).not.toBe(bodies[0].body.session_id);
    expect(bodies[1].body.history).toEqual([]);
  });

  it('请求失败：错误卡可见（用户可读文案）', async () => {
    server.use(
      http.post('*/v1/agents/:key/runs', () =>
        HttpResponse.json(
          { error: { code: 'MODEL_ERROR', message: '模型服务暂时不可用，请稍后再试' } },
          { status: 502 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/你好，我是 LubanAgent/);
    await user.type(screen.getByLabelText('聊天输入'), '你好');
    await user.click(screen.getByRole('button', { name: '发送' }));

    const errorCard = await screen.findByTestId('chat-error-card', {}, { timeout: 3000 });
    expect(errorCard).toHaveTextContent('模型服务暂时不可用，请稍后再试');
  });

  it('停止按钮：断流 + 发出后端 cancel 请求', async () => {
    // 慢流 mock：token 逐帧 500ms，给"停止"留出可点击窗口
    server.use(
      http.post('*/v1/agents/:key/runs', () => {
        const encoder = new TextEncoder();
        const stream = new ReadableStream<Uint8Array>({
          async start(controller) {
            for (let i = 0; i < 20; i++) {
              controller.enqueue(
                encoder.encode(
                  `event: token.delta\ndata: {"run_id":"run-slow","type":"model_token_delta","text":"字${i}"}\n\n`,
                ),
              );
              await new Promise((resolve) => setTimeout(resolve, 500));
            }
            controller.close();
          },
        });
        return new HttpResponse(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }),
      http.post('*/v1/runs/:runId/cancel', () => HttpResponse.json({ cancelled: true })),
    );
    const cancelledRuns: string[] = [];
    server.events.on('request:start', ({ request }) => {
      if (/\/v1\/runs\/.+\/cancel$/.test(request.url)) {
        cancelledRuns.push(request.url.split('/').slice(-2, -1)[0]);
      }
    });

    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/你好，我是 LubanAgent/);
    await user.type(screen.getByLabelText('聊天输入'), '慢慢说');
    await user.click(screen.getByRole('button', { name: '发送' }));

    // 流进行中：显示停止按钮
    expect(await screen.findByRole('button', { name: '停止' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '停止' }));

    // 双杀的服务端半边：POST /v1/runs/{run_id}/cancel（runId 取自事件流）。
    // 本地断流半边（AbortController）在 MSW mock 流下不生效（mock ReadableStream
    // 不响应 abort signal），由真实浏览器环境保证——取消逻辑与调试台同款。
    await waitFor(() => expect(cancelledRuns).toEqual(['run-slow']), { timeout: 3000 });
  });

  it('审批请求：气泡内出现批准/拒绝，点批准发出 decide 请求', async () => {
    overrideRunsWithApproval();
    const { bodies: decideBodies } = captureDecideBodies();
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/你好，我是 LubanAgent/);
    await user.type(screen.getByLabelText('聊天输入'), 'ORD-1001 太贵了退款');
    await user.click(screen.getByRole('button', { name: '发送' }));

    // 审批卡：工具名可见 + 批准/拒绝按钮
    const card = await screen.findByTestId('chat-approval-card', {}, { timeout: 3000 });
    expect(card).toHaveTextContent('refund-apply');
    // 审批 pending 期间输入锁定
    expect(screen.getByLabelText('聊天输入')).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '批准' }));

    await waitFor(() => expect(decideBodies.length).toBe(1), { timeout: 3000 });
    expect(decideBodies[0].id).toBe('appr-001');
    expect(decideBodies[0].body).toEqual({ approved: true, decided_by: 'chat-user' });
  });
});
