/**
 * /agents/:key —— Agent 定义编辑器。
 * 分区表单编辑当前定义；保存 = POST /v1/agents/{key}/versions（version+1，不可变快照）；
 * 右侧版本侧栏可查看任意历史版本的只读形态。
 * 当前 Agent 由 :key 路由参数派生 + react-query 缓存共享，无需额外全局 context。
 */

import { Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { useAgent, useCreateAgentVersion, type AgentDefinition } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

import { AgentForm } from './components/agent-form';
import { VersionSidebar } from './components/version-sidebar';
import { normalizeDefinition, STATUS_LABELS } from './lib/agent-form';

export default function AgentEditorPage() {
  const { key = '' } = useParams();
  const agentQuery = useAgent(key);
  const saveVersion = useCreateAgentVersion(key);

  const [draft, setDraft] = useState<AgentDefinition | null>(null);
  const [viewing, setViewing] = useState<number | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  // 首次加载（或切换 Agent）时用服务端定义初始化草稿；
  // 保存成功后 draft 以 mutation 返回的快照为准，不被 refetch 覆盖
  useEffect(() => {
    if (!agentQuery.data) return;
    setDraft((prev) =>
      prev === null || prev.key !== agentQuery.data.key
        ? normalizeDefinition(agentQuery.data)
        : prev,
    );
  }, [agentQuery.data]);

  useEffect(() => {
    setDraft(null);
    setViewing(null);
    setSavedMessage(null);
  }, [key]);

  const save = () => {
    if (!draft) return;
    setSavedMessage(null);
    saveVersion.mutate(
      { ...draft, version: draft.version + 1 },
      {
        onSuccess: (snapshot) => {
          setDraft(normalizeDefinition(snapshot.definition));
          setSavedMessage(`已保存版本 v${snapshot.version}（不可变快照）`);
        },
      },
    );
  };

  if (agentQuery.isLoading || draft === null) {
    return (
      <div className="space-y-2 p-6 text-sm text-muted-foreground">
        {agentQuery.error ? (
          <>
            <p className="text-destructive">
              加载失败{agentQuery.error.message ? `：${agentQuery.error.message}` : ''}
            </p>
            <p className="text-xs">
              无法连接后端（localhost:8000）或 Agent 不存在？确认后端已启动且 key 正确后重试。
            </p>
            <Button variant="outline" size="sm" onClick={() => agentQuery.refetch()}>
              重试
            </Button>
          </>
        ) : (
          '加载定义中…（长时间无响应可能是后端未启动）'
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="space-y-4 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold tracking-tight">
                Agent 定义编辑器 — {key}
              </h1>
              <Badge variant="secondary">{STATUS_LABELS[draft.status] ?? draft.status}</Badge>
              <Badge variant="outline" className="font-mono">
                v{draft.version}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <Button asChild variant="outline" size="sm">
                <Link to={`/agents/${key}/debug`}>去调试台</Link>
              </Button>
              <Button size="sm" disabled={saveVersion.isPending} onClick={save}>
                <Save className="mr-1.5 h-3.5 w-3.5" />
                {saveVersion.isPending ? '保存中…' : `保存为新版本 (v${draft.version + 1})`}
              </Button>
            </div>
          </div>

          {savedMessage && (
            <p className="rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-sm">
              {savedMessage}
            </p>
          )}
          {saveVersion.error && (
            <p className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {saveVersion.error.message}
            </p>
          )}

          <AgentForm draft={draft} onChange={setDraft} />
        </div>
      </div>

      <VersionSidebar
        agentKey={key}
        currentVersion={draft.version}
        viewing={viewing}
        onView={setViewing}
      />
    </div>
  );
}
