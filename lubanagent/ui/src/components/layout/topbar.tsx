import { Moon, Sun } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { useQueryClient } from '@tanstack/react-query';

import { useHealth } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { AuthStatus } from './auth-status';
import { getTheme, toggleTheme, type Theme } from '@/lib/theme';

const IS_MOCK =
  (import.meta.env.VITE_USE_MOCK ?? (import.meta.env.DEV ? 'true' : 'false')) === 'true';

export function Topbar() {
  const [theme, setTheme] = useState<Theme>(() => getTheme());
  const queryClient = useQueryClient();
  const health = useHealth();
  const connected = health.isSuccess && health.data.status === 'ok';
  const wasConnected = useRef(connected);

  // 后端恢复（断线 → 连通，通常是 dev 重启完成）时失效全部查询：
  // 卡在加载态/错误态的页面自动重拉数据，不需要手动刷新浏览器
  useEffect(() => {
    if (connected && !wasConnected.current) {
      void queryClient.invalidateQueries();
    }
    wasConnected.current = connected;
  }, [connected, queryClient]);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium text-muted-foreground">开发者工作台</span>
        <Badge variant={IS_MOCK ? 'secondary' : 'outline'} className="font-mono text-xs">
          {IS_MOCK ? 'mock' : 'live'}
        </Badge>
      </div>
      <div className="flex items-center gap-2">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span
                  className={
                    connected
                      ? 'h-2 w-2 rounded-full bg-emerald-500'
                      : health.isLoading
                        ? 'h-2 w-2 rounded-full bg-yellow-500'
                        : 'h-2 w-2 rounded-full bg-red-500'
                  }
                />
                API
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {connected ? '后端连通（/health ok）' : health.isLoading ? '探测中…' : '后端不可达'}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <AuthStatus />
        <Button
          variant="ghost"
          size="icon"
          aria-label="切换主题"
          onClick={() => setTheme(toggleTheme())}
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
      </div>
    </header>
  );
}
