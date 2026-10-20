import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';

import { router } from '@/routes';
import { ApiError } from '@/api/client';
import { applyTheme, getTheme } from '@/lib/theme';

import '@/styles.css';

applyTheme(getTheme());

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // dev 期后端常重启：短暂网络失败自动重试（1s 间隔），避免页面停在加载态；
      // 401/403 绝不重试（H13）：凭据缺失/无效需要用户介入，盲重试只会撞出死循环
      retry: (failureCount, error) => {
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          return false;
        }
        return failureCount < 2;
      },
      retryDelay: 1_000,
      // 切回标签页时若数据已过期则自动重拉（后端重启后回到页面即自愈）
      refetchOnWindowFocus: true,
    },
  },
});

/** dev 默认开 mock（VITE_USE_MOCK 缺省 true）；显式 "false" 或生产构建走真后端 */
async function enableMocking(): Promise<void> {
  const flag = import.meta.env.VITE_USE_MOCK ?? (import.meta.env.DEV ? 'true' : 'false');
  if (flag !== 'true') return;
  const { worker } = await import('@/mocks/browser');
  await worker.start({ onUnhandledRequest: 'bypass' });
}

enableMocking().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
});
