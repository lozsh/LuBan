/**
 * Trace 回放（/traces/:runId）：七问摘要卡 + 事件时间线。
 * 摘要数据：Run 记录（GET /v1/runs/{id}，可能缺失则降级）+ 事件流推导。
 * 时间线：GET /v1/traces/{run_id}。
 */

import { ArrowLeft } from 'lucide-react';
import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';

import { useRun, useTrace, type TraceOut } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { formatDateTime } from '@/lib/format';

import { RunStatusBadge } from './TracesPage';
import { TraceTimeline } from './trace-timeline';

type TraceEvent = TraceOut['events'][number];

function payloadRecord(event: TraceEvent): Record<string, unknown> {
  const payload = event.payload;
  return payload !== null && typeof payload === 'object' && !Array.isArray(payload)
    ? (payload as Record<string, unknown>)
    : {};
}

/** 七问摘要卡：Agent 版本 / 实际模型 / 模型调用次数 / Prompt 痕迹 / 工具调用清单 / Token+费用 / 结束原因 */
function TraceSummary({ runId, trace }: { runId: string; trace: TraceOut }) {
  const runQuery = useRun(runId);
  const run = runQuery.data;

  const summary = useMemo(() => {
    const events = trace.events;
    const modelCalls = events.filter((event) => event.type === 'model_call_completed').length;
    const firstModelStart = events.find((event) => event.type === 'model_call_started');
    const promptTrace = firstModelStart ? payloadRecord(firstModelStart).input : undefined;
    const tools = [
      ...new Set(
        events
          .filter((event) => event.type.startsWith('tool_call_'))
          .map((event) => payloadRecord(event).tool)
          .filter((tool): tool is string => typeof tool === 'string'),
      ),
    ];
    const modelFromEvent = firstModelStart ? payloadRecord(firstModelStart).model_key : undefined;
    return { modelCalls, promptTrace, tools, modelFromEvent };
  }, [trace.events]);

  const modelKey =
    run?.model_key ?? (typeof summary.modelFromEvent === 'string' ? summary.modelFromEvent : null);
  const modelCalls =
    run?.token_usage?.model_calls ?? (summary.modelCalls > 0 ? summary.modelCalls : null);
const inputTokens = run?.token_usage?.input_tokens;
const outputTokens = run?.token_usage?.output_tokens;
// H10 用量 schema：usage_known=false = 模型未回报 usage，Token 卡片标注「部分未知」
const usageUnknown = trace.events.some(
  (event) =>
    event.type === 'model_call_completed' && payloadRecord(event).usage_known === false,
);

  return (
    <Card data-testid="trace-summary">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          七问摘要
          {run && <RunStatusBadge status={run.status} />}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
        <div>
          <div className="text-xs text-muted-foreground">Agent · 版本</div>
          <div className="font-mono">{run ? `${run.agent_key} · v${run.agent_version}` : '—'}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">会话 ID</div>
          <div className="break-all font-mono text-xs" title={run?.session_id ?? ''}>
            {run?.session_id ?? '—'}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">用户</div>
          <div className="break-all font-mono text-xs" title={run?.user_id ?? ''}>
            {run?.user_id ?? '—'}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">实际模型</div>
          <div className="font-mono">{modelKey ?? '—'}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">模型调用次数</div>
          <div className="font-mono">{modelCalls ?? '—'}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">结束原因</div>
          <div className="font-mono">{run?.end_reason ?? trace.trace.status}</div>
        </div>
        <div className="col-span-2">
          <div className="text-xs text-muted-foreground">Prompt 痕迹（首次模型调用输入）</div>
          {summary.promptTrace !== undefined ? (
            <pre className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-xs">
              {typeof summary.promptTrace === 'string'
                ? summary.promptTrace
                : JSON.stringify(summary.promptTrace, null, 2)}
            </pre>
          ) : (
            <div className="text-muted-foreground">—（事件未携带）</div>
          )}
        </div>
        <div>
          <div className="text-xs text-muted-foreground">工具调用</div>
          <div className="flex flex-wrap gap-1">
            {summary.tools.length > 0
              ? summary.tools.map((tool) => (
                  <Badge key={tool} variant="secondary" className="font-mono text-xs">
                    {tool}
                  </Badge>
                ))
              : '—'}
          </div>
        </div>
<div>
<div className="text-xs text-muted-foreground">Token · 费用</div>
<div className="font-mono">
{inputTokens !== undefined || outputTokens !== undefined
? `${inputTokens ?? 0} / ${outputTokens ?? 0} · ¥${(run?.cost ?? 0).toFixed(4)}`
: '—'}
{usageUnknown && (
<span data-testid="usage-unknown" className="ml-1 text-xs text-muted-foreground">
（部分未知）
</span>
)}
</div>
</div>
      </CardContent>
    </Card>
  );
}

export default function TraceDetailPage() {
  const { runId = '' } = useParams();
  const traceQuery = useTrace(runId);

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <header className="flex items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/traces">
            <ArrowLeft className="mr-1 h-4 w-4" />
            列表
          </Link>
        </Button>
        <h1 className="text-lg font-semibold">Trace 回放</h1>
        <span className="font-mono text-sm text-muted-foreground">{runId}</span>
        {traceQuery.data && (
          <span className="text-xs text-muted-foreground">
            {formatDateTime(traceQuery.data.trace.started_at)}
          </span>
        )}
        {/* H13 语义三分：查看（本页，只读）/ 重放（mock 录制重放，无副作用）/ 真实再执行
            （Agent 调试台/Chat，会真实烧预算、真实调工具）——本页不提供任何再执行入口 */}
        <span
          className="ml-auto text-xs text-muted-foreground"
          data-testid="trace-readonly-note"
        >
          只读回放：本页不触发重放或再执行；真实再执行请到 Agent 调试台 / 聊天页
        </span>
      </header>

      {traceQuery.isError && (
        <div className="rounded-md border border-red-500/50 p-3 text-sm text-red-500">
          加载失败：{traceQuery.error.message}
        </div>
      )}

      {traceQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}

      {traceQuery.data && (
        <>
          <TraceSummary runId={runId} trace={traceQuery.data} />
          <Separator />
          <section>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              事件时间线（{traceQuery.data.events.length} 条，点击事件展开 payload）
            </h2>
            <TraceTimeline events={traceQuery.data.events} />
          </section>
        </>
      )}
    </div>
  );
}
