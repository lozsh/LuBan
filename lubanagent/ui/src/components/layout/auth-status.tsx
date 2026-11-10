/**
 * 鉴权状态与 API Key 管理入口（H13 工作台鉴权闭环）：
 * - 顶栏徽章：未提供 Key（灰）/ 已提供（绿）/ 最近 401（红，打开对话框引导恢复）；
 * - 对话框：输入/清除内存态 API Key；明示取舍——Key 仅保存在当前页面内存，
 *   不写 localStorage、不进 URL，刷新页面需重新提供；
 * - 401 不自动重试：需要用户更换 Key 或检查可信网关注入，这里提供人工恢复路径。
 */

import { KeyRound, LogOut, UserRound } from 'lucide-react';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { useQueryClient } from '@tanstack/react-query';

import { useLogin, useLogout, useMe } from '@/api/access';
import { authStore, clearAllCredentials, clearApiKey, setApiKey, setSession } from '@/lib/auth';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

export function useAuthState() {
  return useSyncExternalStore(authStore.subscribe, authStore.getSnapshot, authStore.getSnapshot);
}

export function AuthStatus() {
  const { apiKey, sessionToken, rejected } = useAuthState();
  const meQuery = useMe();
  const me = meQuery.data ?? null;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const queryClient = useQueryClient();
  const login = useLogin();
  const logout = useLogout();
  const previousRejected = useRef(false);

  // 最近 401（Key 缺失/失效/网关拒绝）→ 自动弹出恢复对话框（可关闭，不劫持导航）
  // 只在 401 边沿清缓存：后续 401 不再触发 refetch 风暴，恢复凭据后 rejected 复位。
  useEffect(() => {
    if (rejected && !previousRejected.current) {
      queryClient.clear();
      setOpen(true);
    }
    previousRejected.current = rejected;
  }, [rejected, queryClient]);

  function handleSave(): void {
    if (draft.trim() === '') return;
    setApiKey(draft.trim());
    setDraft('');
    setOpen(false);
    // 新身份生效：纪元自增触发整树重挂载，全部查询以新凭据重新拉取；
    // 旧 401 的 query 缓存全部作废。
    queryClient.clear();
  }

  function handleClear(): void {
    clearApiKey();
    setDraft('');
    setOpen(false);
    // 纪元自增触发整树重挂载，清空缓存后由新挂载的查询自行重取。
    queryClient.clear();
  }

  async function handleLogin(): Promise<void> {
    if (username.trim() === '' || password === '') return;
    try {
      const result = await login.mutateAsync({ username: username.trim(), password });
      setSession(result.access_token, result.user);
      setUsername('');
      setPassword('');
      // 切换身份必须清空私密缓存，而不是只标记 stale。
      queryClient.clear();
      // clear 会移除旧账号数据；先回填新身份，让重挂载后的权限闸门
      // 立即命中缓存（数据刚写入视为新鲜，不会重复请求）。
      queryClient.setQueryData(['access', 'me'], result.user);
      // 兜底：epoch 重挂载是主恢复路径；若组件树未随纪元重建（如单测环境），
      // refetchQueries 让仍挂载的 observer 以新凭据重新取数。
      void queryClient.refetchQueries({ type: 'all' });
      setOpen(false);
    } catch {
      // 错误交由登录表单展示。
    }
  }

  function handleLogout(): void {
    logout.mutate(undefined, {
      onSettled: () => {
        clearAllCredentials();
        queryClient.clear();
      },
    });
    setOpen(false);
  }

  const variant = rejected
    ? 'destructive'
    : sessionToken !== null || apiKey !== null
      ? 'default'
      : 'secondary';
  const label = rejected
    ? '鉴权失败'
    : sessionToken !== null
      ? '已登录'
      : apiKey !== null
        ? '已提供 Key'
        : '未提供 Key';
  const identityLabel = me?.auth_enabled === false ? '开发模式' : me?.display_name;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5"
        aria-label="账号与 API Key"
        onClick={() => setOpen(true)}
      >
        <KeyRound className="h-4 w-4" />
        <Badge variant={variant} className="font-mono text-xs" data-testid="auth-status">
          {label}
        </Badge>
        {identityLabel && (
          <Badge variant="outline" className="font-mono text-xs">
            <UserRound className="mr-1 h-3 w-3" />
            {identityLabel}
          </Badge>
        )}
      </Button>
      <DialogContent data-testid="auth-dialog">
        <DialogHeader>
          <DialogTitle>账号与 API Key</DialogTitle>
          <DialogDescription>
            账号登录适合按角色授权；API Key 适合直连调试。两种凭据都只保存在当前页面内存，不写
            localStorage、不进 URL；切换身份后所有私有数据缓存立即清空。
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="account">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="account">账号登录</TabsTrigger>
            <TabsTrigger value="api-key">API Key</TabsTrigger>
          </TabsList>
          <TabsContent value="account" className="space-y-3">
            {me?.auth_enabled === false ? (
              <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
                当前是开发模式（AUTH_ENABLED=false），服务端已授出全部权限。
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor="login-username">用户名</Label>
                  <Input
                    id="login-username"
                    autoComplete="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    data-testid="login-username"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="login-password">密码</Label>
                  <Input
                    id="login-password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void handleLogin();
                    }}
                    data-testid="login-password"
                  />
                </div>
                {login.isError && (
                  <p className="text-sm text-red-500" data-testid="login-error">
                    登录失败：{login.error.message}
                  </p>
                )}
                <div className="flex items-center gap-2">
                  <Button
                    onClick={() => void handleLogin()}
                    disabled={login.isPending}
                    data-testid="login-submit"
                  >
                    登录
                  </Button>
                  {me?.auth_enabled && (
                    <Button variant="outline" onClick={handleLogout} data-testid="auth-logout">
                      <LogOut className="mr-1 h-4 w-4" />
                      退出登录
                    </Button>
                  )}
                </div>
              </>
            )}
          </TabsContent>
          <TabsContent value="api-key" className="space-y-2" forceMount>
            <Label htmlFor="api-key-input">API Key</Label>
            <Input
              id="api-key-input"
              type="password"
              autoComplete="off"
              placeholder={apiKey !== null ? '已设置（输入可更换）' : 'sk-…'}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleSave();
              }}
              data-testid="api-key-input"
            />
            <p className="text-xs text-muted-foreground">
              保存后立即生效；401 不自动重试，更换 Key 后所有查询缓存清空并重新拉取。
            </p>
          </TabsContent>
        </Tabs>
        <DialogFooter>
          {apiKey !== null && (
            <Button variant="outline" onClick={handleClear} data-testid="auth-clear">
              清除
            </Button>
          )}
          <Button onClick={handleSave} disabled={draft.trim() === ''} data-testid="auth-save">
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
