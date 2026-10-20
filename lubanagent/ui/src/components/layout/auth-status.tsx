/**
 * 鉴权状态与 API Key 管理入口（H13 工作台鉴权闭环）：
 * - 顶栏徽章：未提供 Key（灰）/ 已提供（绿）/ 最近 401（红，打开对话框引导恢复）；
 * - 对话框：输入/清除内存态 API Key；明示取舍——Key 仅保存在当前页面内存，
 *   不写 localStorage、不进 URL，刷新页面需重新提供；
 * - 401 不自动重试：需要用户更换 Key 或检查可信网关注入，这里提供人工恢复路径。
 */

import { KeyRound } from 'lucide-react';
import { useEffect, useState, useSyncExternalStore } from 'react';

import { useQueryClient } from '@tanstack/react-query';

import { authStore, clearApiKey, setApiKey } from '@/lib/auth';
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

export function useAuthState() {
  return useSyncExternalStore(authStore.subscribe, authStore.getSnapshot, authStore.getSnapshot);
}

export function AuthStatus() {
  const { apiKey, rejected } = useAuthState();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const queryClient = useQueryClient();

  // 最近 401（Key 缺失/失效/网关拒绝）→ 自动弹出恢复对话框（可关闭，不劫持导航）
  useEffect(() => {
    if (rejected) setOpen(true);
  }, [rejected]);

  function handleSave(): void {
    if (draft.trim() === '') return;
    setApiKey(draft.trim());
    setDraft('');
    setOpen(false);
    // 新身份生效：全部查询重新拉取（旧 401 的 query 缓存全部作废）
    void queryClient.invalidateQueries();
  }

  function handleClear(): void {
    clearApiKey();
    setDraft('');
    setOpen(false);
  }

  const variant = rejected ? 'destructive' : apiKey !== null ? 'default' : 'secondary';
  const label = rejected ? '鉴权失败' : apiKey !== null ? '已提供 Key' : '未提供 Key';

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        variant="ghost"
        size="sm"
        className="gap-1.5"
        aria-label="管理 API Key"
        onClick={() => setOpen(true)}
      >
        <KeyRound className="h-4 w-4" />
        <Badge variant={variant} className="font-mono text-xs" data-testid="auth-status">
          {label}
        </Badge>
      </Button>
      <DialogContent data-testid="auth-dialog">
        <DialogHeader>
          <DialogTitle>API Key（直连鉴权）</DialogTitle>
          <DialogDescription>
            后端开启鉴权（AUTH_ENABLED=true）时，所有 /v1 请求需要
            Authorization: Bearer &lt;key&gt;。Key 仅保存在当前页面内存中，不写入
            localStorage、不进 URL、不进构建产物；刷新页面后需重新提供。若走可信网关
            （网关统一注入身份），此处无需填写，出现鉴权失败请检查网关配置。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
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
            保存后立即对所有 REST/SSE/上传请求生效；401 不会自动重试，更换 Key 后数据自动重新拉取。
          </p>
        </div>
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
