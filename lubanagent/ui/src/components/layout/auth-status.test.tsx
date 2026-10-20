/** H13 顶栏鉴权闭环：状态徽章、Key 设置/清除、401 → 弹窗恢复 → 查询重拉。 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { clearApiKey, markUnauthorized } from '@/lib/auth';

import { AuthStatus } from './auth-status';

function renderAuthStatus() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthStatus />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  clearApiKey();
});

describe('AuthStatus（顶栏鉴权入口）', () => {
  it('默认未提供 Key；设置后徽章变为已提供', async () => {
    const user = userEvent.setup();
    renderAuthStatus();
    expect(screen.getByTestId('auth-status')).toHaveTextContent('未提供 Key');

    await user.click(screen.getByRole('button', { name: '管理 API Key' }));
    await user.type(screen.getByTestId('api-key-input'), 'sk-new');
    await user.click(screen.getByTestId('auth-save'));

    expect(screen.getByTestId('auth-status')).toHaveTextContent('已提供 Key');
    expect(screen.queryByTestId('auth-dialog')).not.toBeInTheDocument();
  });

  it('401 登记后自动弹窗提示鉴权失败；换 Key 后 rejected 复位', async () => {
    const user = userEvent.setup();
    renderAuthStatus();
    markUnauthorized();

    await waitFor(() => expect(screen.getByTestId('auth-dialog')).toBeInTheDocument());
    expect(screen.getByTestId('auth-status')).toHaveTextContent('鉴权失败');

    await user.type(screen.getByTestId('api-key-input'), 'sk-recovery');
    await user.click(screen.getByTestId('auth-save'));
    expect(screen.getByTestId('auth-status')).toHaveTextContent('已提供 Key');
  });

  it('清除 Key 回到未提供状态（登出语义；仅内存，无持久化痕迹）', async () => {
    const user = userEvent.setup();
    renderAuthStatus();
    await user.click(screen.getByRole('button', { name: '管理 API Key' }));
    await user.type(screen.getByTestId('api-key-input'), 'sk-temp');
    await user.click(screen.getByTestId('auth-save'));
    expect(screen.getByTestId('auth-status')).toHaveTextContent('已提供 Key');

    await user.click(screen.getByRole('button', { name: '管理 API Key' }));
    await user.click(screen.getByTestId('auth-clear'));
    expect(screen.getByTestId('auth-status')).toHaveTextContent('未提供 Key');
  });
});
