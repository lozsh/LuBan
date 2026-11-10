import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { securityUsers } from '@/mocks/fixtures';
import { server } from '@/mocks/node';
import { routeObjects } from '@/routes';

function renderSecurity() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routeObjects, { initialEntries: ['/security'] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe('用户权限页', () => {
  it('展示用户、角色和权限点；admin 保持受保护', async () => {
    renderSecurity();
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(screen.getByTestId('security-user-bob')).toBeInTheDocument();
    expect(screen.getByTestId('security-role-admin')).toBeInTheDocument();
    const adminPermission = document.querySelector<HTMLInputElement>(
      '#role-admin-security\\:manage',
    );
    expect(adminPermission).not.toBeNull();
    expect(adminPermission).toBeDisabled();
  });

  it('新建用户并分配角色', async () => {
    const user = userEvent.setup();
    renderSecurity();
    expect(await screen.findByText('Alice')).toBeInTheDocument();

    await user.type(screen.getByLabelText('用户名'), 'carol');
    await user.type(screen.getByLabelText('显示名'), 'Carol');
    await user.type(screen.getByLabelText('初始密码'), 'secret123');
    const viewerCheckbox = document.querySelector<HTMLInputElement>('#new-user-role-viewer');
    expect(viewerCheckbox).not.toBeNull();
    await user.click(viewerCheckbox!);
    await user.click(screen.getByRole('button', { name: '新建用户' }));

    expect(await screen.findByTestId('security-user-carol')).toBeInTheDocument();
    expect(screen.getByTestId('security-user-carol')).toHaveTextContent('Carol');
  });

  it('用户读取失败时显示错误，而不是让用户表看起来是空列表', async () => {
    server.use(
      http.get('*/v1/security/users', () =>
        HttpResponse.json(
          { error: { code: 'DB_ERROR', message: 'user store unavailable' } },
          { status: 500 },
        ),
      ),
    );
    renderSecurity();
    expect(await screen.findByText('数据读取失败：user store unavailable')).toBeInTheDocument();
    expect(screen.queryByTestId('security-user-alice')).not.toBeInTheDocument();
  });

  it('重置密码走 PATCH 并刷新当前账号权限缓存', async () => {
    const user = userEvent.setup();
    const patchBodies: Array<Record<string, unknown>> = [];
    let meCalls = 0;
    server.use(
      http.get('*/v1/auth/me', () => {
        meCalls += 1;
        return HttpResponse.json({
          subject: 'user:1',
          id: 'u-admin',
          username: 'alice',
          display_name: 'Alice',
          roles: ['admin'],
          role_keys: ['admin'],
          permissions: ['*'],
          auth_enabled: true,
          is_bootstrap: true,
        });
      }),
      http.patch('*/v1/security/users/:id', async ({ request }) => {
        patchBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(securityUsers[0]);
      }),
    );
    renderSecurity();
    expect(await screen.findByTestId('security-user-alice')).toBeInTheDocument();

    const aliceRow = screen.getByTestId('security-user-alice');
    await user.click(within(aliceRow).getByRole('button', { name: '重置密码' }));
    await user.type(screen.getByTestId('reset-password-input'), 'new-password-123');
    await user.click(screen.getByTestId('reset-password-submit'));

    await waitFor(() => expect(patchBodies).toEqual([{ password: 'new-password-123' }]));
    await waitFor(() => expect(meCalls).toBeGreaterThan(1));
    expect(screen.queryByTestId('reset-password-dialog')).not.toBeInTheDocument();
  });

  it('同一用户的角色勾选请求未完成前禁用，避免快速点击造成旧 props 竞态', async () => {
    const user = userEvent.setup();
    let patchCalls = 0;
    server.use(
      http.patch('*/v1/security/users/:id', async () => {
        patchCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 40));
        return HttpResponse.json(securityUsers[1]);
      }),
    );
    renderSecurity();
    expect(await screen.findByTestId('security-user-bob')).toBeInTheDocument();

    const checkbox = document.querySelector<HTMLInputElement>('#user-u-operator-role-viewer');
    expect(checkbox).not.toBeNull();
    await user.click(checkbox!);
    expect(checkbox).toBeDisabled();

    await user.click(checkbox!);
    await waitFor(() => expect(checkbox).not.toBeDisabled());
    expect(patchCalls).toBe(1);
  });

  it('角色权限变更后刷新 access/me，让当前账号 UI 权限同步', async () => {
    const user = userEvent.setup();
    let meCalls = 0;
    let rolePatch: Record<string, unknown> | null = null;
    server.use(
      http.get('*/v1/auth/me', () => {
        meCalls += 1;
        return HttpResponse.json({
          subject: 'user:1',
          id: 'u-admin',
          username: 'alice',
          display_name: 'Alice',
          roles: ['admin'],
          role_keys: ['admin'],
          permissions: ['*'],
          auth_enabled: true,
          is_bootstrap: true,
        });
      }),
      http.patch('*/v1/security/roles/:key', async ({ request }) => {
        rolePatch = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          key: 'operator',
          name: '操作员',
          protected: false,
          permissions: ['config:read', 'config:write', 'run:read'],
        });
      }),
    );
    renderSecurity();
    const operatorCard = await screen.findByTestId('security-role-operator');
    expect(meCalls).toBeGreaterThan(0);

    await user.click(within(operatorCard).getByLabelText('修改配置（config:write）'));

    await waitFor(() => expect(rolePatch).not.toBeNull());
    expect(rolePatch).toMatchObject({ name: '操作员' });
    await waitFor(() => expect(meCalls).toBeGreaterThan(1));
  });
});
