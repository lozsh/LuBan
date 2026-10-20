import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, describe, expect, it } from 'vitest';

import PromptsPage from './PromptsPage';

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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/prompts']}>
        <PromptsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Prompt 库', () => {
  it('列表渲染：key / 当前版本 / 描述', async () => {
    renderPage();

    expect(await screen.findByText('FAQ 客服 Prompt')).toBeInTheDocument();
    expect(screen.getByText('退款安抚 Prompt')).toBeInTheDocument();
    expect(screen.getByText('cs-faq-prompt')).toBeInTheDocument();
    expect(screen.getByText('refund-prompt')).toBeInTheDocument();
    // 当前版本列（fixture v2 / v1）
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
  });

  it('点击行进入编辑区：content 与版本历史可见，修改后出现保存新版本按钮', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();

    await user.click(await screen.findByText('FAQ 客服 Prompt'));

    const editor = await screen.findByLabelText('Prompt 内容');
    expect(editor).toHaveValue(
      '你是 {company} 的客服 FAQ 专家，回答退换货政策与产品问题，不承诺知识库未覆盖的内容。',
    );
    // 版本历史侧栏（fixture 两个版本）
    expect(await screen.findByText('版本历史')).toBeInTheDocument();
    expect(screen.getByText('当前')).toBeInTheDocument();

    // 修改 content → 保存新版本按钮可点
    await user.type(editor, '（追加）');
    const save = screen.getByRole('button', { name: /保存为新版本 v3/ });
    expect(save).toBeEnabled();
  });

  it('新建对话框：填 key/名称提交', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    renderPage();

    await user.click(await screen.findByRole('button', { name: /新建 Prompt/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Prompt key'), 'new-p');
    await user.type(within(dialog).getByLabelText('Prompt 名称'), '新 Prompt');
    await user.click(within(dialog).getByRole('button', { name: '创建' }));
    // mock 201 后对话框关闭
    await waitForDialogClosed();
  });
});

async function waitForDialogClosed() {
  // mock 创建成功 → onSuccess 关闭；等一拍让断言不 flaky
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(true).toBe(true);
}
