/**
 * /models/compare 模型对比：选一个 Agent + 一段输入 + 两个配置标签（brain profile），
 * 同一输入两次 SSE 试跑并排渲染：输出 / token / 耗时 / 结束原因。
 * 注：v1 runs API 不接受 profile 覆盖，标签仅作本次试跑的标识备注。
 */

import { Play } from 'lucide-react';
import { useEffect, useState } from 'react';

import { API_BASE } from '@/api/client';
import { useAgents, useRun, type BrainProfile, type Run } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { durationBetween, formatDurationMs } from '@/lib/format';
import { useRunStream, type RunStream, type SseEvent } from '@/lib/sse';

const PROFILES: BrainProfile[] = ['fast', 'smart', 'cheap', 'vision'];

const STREAM_STATUS_TEXT: Record<RunStream['status'], string> = {
  idle: '待开始',
  streaming: '流式中',
  done: '完成',
  cancelled: '已取消',
  error: '失败',
};

function runIdOf(events: SseEvent[]): string {
  for (const event of events) {
    const id = (event.data as { run_id?: unknown })?.run_id;
    if (typeof id === 'string') return id;
  }
  return '';
}

function streamText(events: SseEvent[]): string {
  return events
    .filter((event) => event.event === 'token.delta')
    .map((event) => (event.data as { text?: string }).text ?? '')
    .join('');
}

function toolLines(events: SseEvent[]): string[] {
  return events
    .filter((event) => event.event === 'tool.started' || event.event === 'tool.finished')
    .map((event) => {
      const data = event.data as { tool?: string; duration_ms?: number };
      return event.event === 'tool.started'
        ? `调用工具 ${data.tool ?? '?'}`
        : `工具 ${data.tool ?? '?'} 完成（${data.duration_ms ?? 0}ms）`;
    });
}

function ComparePanel({
  label,
  stream,
  run,
}: {
  label: BrainProfile;
  stream: RunStream;
  run: Run | undefined;
}) {
  const text = streamText(stream.events);
  const lines = toolLines(stream.events);
  const completed = stream.events.find((event) => event.event === 'run.completed');
  const endReason =
    run?.end_reason ?? (completed?.data as { end_reason?: string } | undefined)?.end_reason;

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            配置 <Badge className="font-mono">{label}</Badge>
          </CardTitle>
          <Badge variant={stream.status === 'done' ? 'default' : 'outline'}>
            {STREAM_STATUS_TEXT[stream.status]}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-3">
        <div className="min-h-32 rounded-md border bg-muted/30 p-3 text-sm whitespace-pre-wrap">
          {text || run?.output || (
            <span className="text-muted-foreground">
              {stream.status === 'idle' ? '尚未试跑' : '等待输出…'}
            </span>
          )}
        </div>
        {lines.length > 0 && (
          <ul className="space-y-0.5 text-xs text-muted-foreground">
            {lines.map((line, index) => (
              <li key={index}>· {line}</li>
            ))}
          </ul>
        )}
        {stream.error && <p className="text-sm text-destructive">{stream.error}</p>}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 border-t pt-3 text-xs">
          <dt className="text-muted-foreground">结束原因</dt>
          <dd className="font-mono">{endReason ?? '—'}</dd>
          <dt className="text-muted-foreground">模型</dt>
          <dd className="font-mono">{run?.model_key ?? '—'}</dd>
          <dt className="text-muted-foreground">Token（入/出）</dt>
          <dd className="font-mono">
            {run?.token_usage
              ? `${run.token_usage.input_tokens} / ${run.token_usage.output_tokens}`
              : '—'}
          </dd>
          <dt className="text-muted-foreground">耗时</dt>
          <dd className="font-mono">
            {formatDurationMs(durationBetween(run?.started_at, run?.ended_at))}
          </dd>
        </dl>
      </CardContent>
    </Card>
  );
}

export default function ModelComparePage() {
  const agentsQuery = useAgents();
  const [agentKey, setAgentKey] = useState('');
  const [input, setInput] = useState('');
  const [labelA, setLabelA] = useState<BrainProfile>('smart');
  const [labelB, setLabelB] = useState<BrainProfile>('fast');

  const streamA = useRunStream();
  const streamB = useRunStream();
  const [doneIdA, setDoneIdA] = useState('');
  const [doneIdB, setDoneIdB] = useState('');

  useEffect(() => {
    if (streamA.status === 'done') setDoneIdA(runIdOf(streamA.events));
  }, [streamA.status, streamA.events]);
  useEffect(() => {
    if (streamB.status === 'done') setDoneIdB(runIdOf(streamB.events));
  }, [streamB.status, streamB.events]);

  const runA = useRun(doneIdA);
  const runB = useRun(doneIdB);

  const running = streamA.status === 'streaming' || streamB.status === 'streaming';
  const canStart = agentKey !== '' && input.trim() !== '' && !running;

  function handleStart(): void {
    setDoneIdA('');
    setDoneIdB('');
    const url = `${API_BASE}/v1/agents/${agentKey}/runs`;
    const body = { input: input.trim() };
    void streamA.start(url, body);
    void streamB.start(url, body);
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">模型对比</h1>
        <p className="text-sm text-muted-foreground">
          同一 Agent、同一输入，两次试跑并排对比输出 / token / 耗时 / 结束原因。
        </p>
      </div>

      <div className="grid gap-3 rounded-md border p-4 lg:grid-cols-[1fr_1fr_1fr_auto]">
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Agent</p>
          <Select value={agentKey} onValueChange={setAgentKey}>
            <SelectTrigger aria-label="对比 Agent" className="font-mono text-xs">
              <SelectValue placeholder="选择 Agent" />
            </SelectTrigger>
            <SelectContent>
              {(agentsQuery.data ?? []).map((agent) => (
                <SelectItem key={agent.key} value={agent.key} className="font-mono text-xs">
                  {agent.key}（{agent.name}）
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">配置标签 A</p>
          <Select value={labelA} onValueChange={(value) => setLabelA(value as BrainProfile)}>
            <SelectTrigger aria-label="配置标签 A" className="font-mono text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROFILES.map((profile) => (
                <SelectItem key={profile} value={profile} className="font-mono text-xs">
                  {profile}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">配置标签 B</p>
          <Select value={labelB} onValueChange={(value) => setLabelB(value as BrainProfile)}>
            <SelectTrigger aria-label="配置标签 B" className="font-mono text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROFILES.map((profile) => (
                <SelectItem key={profile} value={profile} className="font-mono text-xs">
                  {profile}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-end">
          <Button onClick={handleStart} disabled={!canStart}>
            <Play className="mr-1 h-4 w-4" />
            {running ? '试跑中…' : '开始对比'}
          </Button>
        </div>
        <div className="lg:col-span-4">
          <Textarea
            aria-label="对比输入"
            placeholder="输入一段用户消息，两侧同时试跑…"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            rows={2}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            标签仅标识本次试跑；v1 runs API 暂不接受 profile 覆盖，实际槽位以 Agent 大脑配置为准。
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ComparePanel label={labelA} stream={streamA} run={runA.data} />
        <ComparePanel label={labelB} stream={streamB} run={runB.data} />
      </div>
    </div>
  );
}
