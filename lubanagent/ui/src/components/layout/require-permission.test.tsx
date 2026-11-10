import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { routeObjects } from '@/routes';
import { server } from '@/mocks/node';

describe('路由权限闸门', () => {
  it('缺少 security:manage 时直接访问 /security 返回 403', async () => {
    server.use(
      http.get('*/v1/auth/me', () =>
        HttpResponse.json({
          subject: 'u-operator',
          username: 'bob',
          display_name: 'Bob',
          roles: ['operator'],
          permissions: ['config:read', 'run:read', 'run:execute', 'diagnostics:read'],
          auth_enabled: true,
          is_bootstrap: false,
        }),
      ),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter(routeObjects, { initialEntries: ['/security'] });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId('forbidden-page')).toBeInTheDocument();
    expect(screen.getByText('403 权限不足')).toBeInTheDocument();
  });
});
