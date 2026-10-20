/**
 * 版本侧栏：列出 v1..当前版（倒序），标注"当前版"；
 * 点击任意版本经 GET /v1/agents/{key}/versions/{v} 拉取不可变快照，只读展示。
 */

import { X } from 'lucide-react';

import { useAgentVersion } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { formatDateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

import { STATUS_LABELS } from '../lib/agent-form';

interface VersionSidebarProps {
  agentKey: string;
  currentVersion: number;
  viewing: number | null;
  onView: (version: number | null) => void;
}

export function VersionSidebar({ agentKey, currentVersion, viewing, onView }: VersionSidebarProps) {
  const versions = Array.from({ length: currentVersion }, (_, i) => currentVersion - i);

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l bg-card/30">
      <div className="border-b px-4 py-3">
        <h2 className="text-sm font-semibold">版本历史</h2>
        <p className="text-xs text-muted-foreground">保存即产生不可变新版本</p>
      </div>
      <div className="flex flex-wrap gap-1.5 border-b p-3">
        {versions.map((version) => (
          <button
            key={version}
            type="button"
            onClick={() => onView(version === viewing ? null : version)}
            className={cn(
              'inline-flex items-center gap-1 rounded-md border px-2 py-1 font-mono text-xs transition-colors',
              viewing === version
                ? 'border-primary bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
            )}
          >
            v{version}
            {version === currentVersion && (
              <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                当前版
              </Badge>
            )}
          </button>
        ))}
      </div>
      {viewing !== null && viewing <= currentVersion ? (
        <VersionViewer agentKey={agentKey} version={viewing} onClose={() => onView(null)} />
      ) : (
        <p className="p-4 text-xs text-muted-foreground">点击版本号查看该版本的只读快照。</p>
      )}
    </aside>
  );
}

function VersionViewer({
  agentKey,
  version,
  onClose,
}: {
  agentKey: string;
  version: number;
  onClose: () => void;
}) {
  const query = useAgentVersion(agentKey, version);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="font-mono text-xs font-semibold">v{version} 只读快照</span>
        <Button type="button" variant="ghost" size="sm" aria-label="关闭快照" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-4 text-xs">
          {query.isLoading && <p className="text-muted-foreground">加载中…</p>}
          {query.error && <p className="text-destructive">{query.error.message}</p>}
          {query.data && (
            <>
              <div className="flex items-center gap-2">
                <Badge variant="outline">
                  {STATUS_LABELS[query.data.status] ?? query.data.status}
                </Badge>
                <span className="text-muted-foreground">
                  {formatDateTime(query.data.published_at)}
                </span>
              </div>
              <div>
                <p className="mb-1 font-medium text-muted-foreground">名称</p>
                <p>{query.data.definition.name}</p>
              </div>
              <div>
                <p className="mb-1 font-medium text-muted-foreground">指令</p>
                <p className="whitespace-pre-wrap rounded-md border bg-muted/30 p-2 font-mono">
                  {query.data.definition.instructions || '（空）'}
                </p>
              </div>
              <div>
                <p className="mb-1 font-medium text-muted-foreground">完整定义（JSON）</p>
                <pre className="overflow-x-auto rounded-md border bg-muted/30 p-2 font-mono leading-relaxed">
                  {JSON.stringify(query.data.definition, null, 2)}
                </pre>
              </div>
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
