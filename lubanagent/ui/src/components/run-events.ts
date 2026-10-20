/**
 * Run SSE 事件流 → 渲染模型（调试台 / Prompt 调试共用）。
 * 事件类型以 data.type（snake_case TraceEventType，后端 sse_stream 外发格式）为准，
 * event 帧名（dotted）兜底。
 */

import type { SseEvent } from '@/lib/sse';

export interface ToolCallEntry {
  id: string;
  tool: string;
  args: unknown;
  status: 'running' | 'done' | 'failed';
  durationMs: number | null;
  result: unknown;
  error: unknown;
}

export interface StreamNotice {
  type: string;
  data: Record<string, unknown>;
}

export interface ReducedRun {
  runId: string | null;
  /** token.delta 打字机累积文本 */
  text: string;
  tools: ToolCallEntry[];
  fallbacks: Record<string, unknown>[];
  /** budget / approval 提示条 */
  notices: StreamNotice[];
  endReason: string | null;
  output: string | null;
  failure: { code: string; message: string } | null;
  cancelled: boolean;
  /** 已收到终态帧（run.completed / run.failed / run.cancelled） */
  finished: boolean;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** 事件类型：优先 data.type（snake_case），兜底帧名（dotted） */
export function eventTypeOf(event: SseEvent): string {
  const data = asRecord(event.data);
  return typeof data.type === 'string' ? data.type : event.event;
}

export function reduceRunEvents(events: SseEvent[]): ReducedRun {
  const result: ReducedRun = {
    runId: null,
    text: '',
    tools: [],
    fallbacks: [],
    notices: [],
    endReason: null,
    output: null,
    failure: null,
    cancelled: false,
    finished: false,
  };
  // 同名工具可能并发/多次调用：按名维持队列，完成帧按序出队配对
  const runningTools = new Map<string, ToolCallEntry[]>();

  for (const event of events) {
    const data = asRecord(event.data);
    const type = eventTypeOf(event);
    if (result.runId === null && typeof data.run_id === 'string') result.runId = data.run_id;

    switch (type) {
      case 'model_token_delta':
        if (typeof data.text === 'string') result.text += data.text;
        break;
      case 'tool_call_started': {
        const entry: ToolCallEntry = {
          id: `tool-${result.tools.length}`,
          tool: typeof data.tool === 'string' ? data.tool : 'unknown',
          args: data.args ?? null,
          status: 'running',
          durationMs: null,
          result: null,
          error: null,
        };
        result.tools.push(entry);
        const queue = runningTools.get(entry.tool) ?? [];
        queue.push(entry);
        runningTools.set(entry.tool, queue);
        break;
      }
      case 'tool_call_completed':
      case 'tool_call_failed': {
        const queue = runningTools.get(typeof data.tool === 'string' ? data.tool : '');
        const entry = queue?.shift();
        if (entry) {
          entry.status = type === 'tool_call_completed' ? 'done' : 'failed';
          entry.durationMs = typeof data.duration_ms === 'number' ? data.duration_ms : null;
          entry.result = data.result ?? null;
          entry.error = data.error ?? null;
        }
        break;
      }
      case 'model_fallback':
        result.fallbacks.push(data);
        break;
      case 'budget_warning':
      case 'budget_exceeded':
      case 'tool_approval_requested':
      case 'tool_approval_resolved':
        result.notices.push({ type, data });
        break;
      case 'run_completed':
        result.finished = true;
        result.endReason = typeof data.end_reason === 'string' ? data.end_reason : 'final_answer';
        result.output = typeof data.output === 'string' ? data.output : null;
        break;
      case 'run_failed': {
        result.finished = true;
        result.endReason = 'error';
        const error = asRecord(data.error);
        result.failure = {
          code: typeof error.code === 'string' ? error.code : 'RUN_FAILED',
          message: typeof error.message === 'string' ? error.message : '运行失败',
        };
        break;
      }
      case 'run_cancelled':
        result.finished = true;
        result.cancelled = true;
        result.endReason = 'cancelled';
        break;
      default:
        break;
    }
  }
  return result;
}
