/**
 * Trace 事件时间线（Trace 回放页私有）：
 * 类型着色、相对时间轴（+xxxms）、step_index 分组、点击展开 payload JSON 树。
 */

import { useMemo, useState } from 'react';

import type { TraceOut } from '@/api/hooks';
import { cn } from '@/lib/utils';

import { JsonTree } from './json-tree';

type TraceEvent = TraceOut['events'][number];

/** 事件类型 → 着色（点 + 文字） */
const TYPE_CLASS: Record<string, string> = {
  run_started: 'bg-blue-500',
  run_completed: 'bg-emerald-500',
  run_failed: 'bg-red-500',
  run_cancelled: 'bg-slate-400',
  step_started: 'bg-slate-500',
  step_completed: 'bg-slate-500',
  model_call_started: 'bg-violet-500',
  model_token_delta: 'bg-violet-400',
  model_call_completed: 'bg-violet-500',
  tool_call_started: 'bg-amber-500',
  tool_call_completed: 'bg-amber-500',
  tool_call_failed: 'bg-red-500',
  tool_approval_requested: 'bg-cyan-500',
  tool_approval_resolved: 'bg-cyan-500',
  budget_warning: 'bg-orange-500',
  budget_exceeded: 'bg-orange-600',
  model_fallback: 'bg-yellow-500',
};

function dotClass(type: string): string {
  return TYPE_CLASS[type] ?? 'bg-muted-foreground';
}

/** 事件行摘要：从 payload 提取最有辨识度的一段 */
function summarize(event: TraceEvent): string {
  const payload = event.payload ?? {};
  const text = payload.text;
  if (typeof text === 'string') return text.length > 48 ? `${text.slice(0, 48)}…` : text;
  const tool = payload.tool;
  if (typeof tool === 'string') return tool;
  const model = payload.model_key;
  if (typeof model === 'string') return model;
  const endReason = payload.end_reason;
  if (typeof endReason === 'string') return endReason;
  const error = payload.error;
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as { message?: unknown }).message ?? '');
  }
  return '';
}

function relativeMs(base: number, at: string): string {
  const t = new Date(at).getTime();
  if (Number.isNaN(t) || Number.isNaN(base)) return '';
  const delta = Math.max(0, t - base);
  return delta < 1000 ? `+${delta}ms` : `+${(delta / 1000).toFixed(2)}s`;
}

interface EventGroup {
  /** null = Run 级（无 step） */
  step: number | null;
  events: TraceEvent[];
}

function groupByStep(events: TraceEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  let current: EventGroup | null = null;
  for (const event of events) {
    const step = event.step_index ?? null;
    if (current === null || current.step !== step) {
      current = { step, events: [] };
      groups.push(current);
    }
    current.events.push(event);
  }
  return groups;
}

function EventRow({ event, base }: { event: TraceEvent; base: number }) {
  const [open, setOpen] = useState(false);
  const summary = summarize(event);
  return (
    <div>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-muted/60"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
      >
        <span className={cn('h-2 w-2 shrink-0 rounded-full', dotClass(event.type))} />
        <span className="w-20 shrink-0 font-mono text-xs text-muted-foreground">
          {relativeMs(base, event.created_at)}
        </span>
        <span className="shrink-0 font-mono text-xs font-medium">{event.type}</span>
        {summary !== '' && (
          <span className="truncate text-xs text-muted-foreground">{summary}</span>
        )}
      </button>
      {open && (
        <div className="ml-6 rounded border bg-muted/30 p-2">
          <JsonTree value={event.payload ?? {}} />
        </div>
      )}
    </div>
  );
}

export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  const groups = useMemo(() => groupByStep(events), [events]);
  const base = events.length > 0 ? new Date(events[0].created_at).getTime() : Number.NaN;

  return (
    <div className="space-y-3" data-testid="trace-timeline">
      {groups.map((group, index) => (
        <div key={index}>
          <div className="mb-1 flex items-center gap-2">
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              {group.step === null ? 'Run 级' : `步骤 ${group.step}`}
            </span>
            <span className="h-px flex-1 bg-border" />
          </div>
          <div className="space-y-0.5">
            {group.events.map((event) => (
              <EventRow key={event.id} event={event} base={base} />
            ))}
          </div>
        </div>
      ))}
      {events.length === 0 && (
        <p className="py-6 text-center text-sm text-muted-foreground">该 Run 暂无事件。</p>
      )}
    </div>
  );
}
