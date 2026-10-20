/**
 * SSE 客户端：fetch + ReadableStream 逐行解析 `event:`/`data:` 帧。
 * 不引 Vercel AI SDK（契约 D4-1 决策 5）——协议是后端 sse_stream 自定义格式。
 */

import { useCallback, useRef, useState } from 'react';

import { authHeaders, isUnauthorizedStatus, markUnauthorized } from '@/lib/auth';

/** 一个解析完成的 SSE 帧；data 已尽力 JSON.parse，失败保留原始文本 */
export interface SseEvent<T = unknown> {
  event: string;
  data: T;
  raw: string;
}

/** 解析单个 SSE 帧（不含末尾空行）；无 data 行返回 null */
export function parseSseFrame(frame: string): SseEvent | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line === '' || line.startsWith(':')) continue; // 注释/心跳
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trimStart();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''));
    }
  }
  if (dataLines.length === 0) return null;
  const text = dataLines.join('\n');
  let data: unknown = text;
  try {
    data = JSON.parse(text);
  } catch {
    // 非 JSON 载荷保留原文
  }
  return { event, data, raw: text };
}

/** 把 ReadableStream 解析为 SSE 帧的异步迭代器（跨 chunk 缓冲、CRLF 兼容） */
export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseSseFrame(frame);
        if (parsed) yield parsed;
        boundary = buffer.indexOf('\n\n');
      }
    }
    buffer += decoder.decode();
    const tail = parseSseFrame(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

export interface SsePostOptions {
  /** AbortSignal 外部接管；未传时由调用方自行取消 */
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

/** POST JSON 并逐帧回调 SSE 事件；非 2xx 抛错（尽力解析 ErrorOut 形状）。
 * 鉴权（H13）：自动合并内存态 Authorization 头（调用方可覆盖）；401 统一登记不自动重试。 */
export async function streamSsePost(
  url: string,
  body: unknown,
  onEvent: (event: SseEvent) => void,
  options: SsePostOptions = {},
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...options.headers },
    body: JSON.stringify(body ?? {}),
    signal: options.signal ?? null,
  });
  if (!response.ok) {
    if (isUnauthorizedStatus(response.status)) markUnauthorized();
    let message = `请求失败: ${response.status}`;
    try {
      const payload = (await response.json()) as { error?: { message?: string } };
      if (payload.error?.message) message = payload.error.message;
    } catch {
      // 保留状态码兜底文案
    }
    const error = new Error(message) as Error & { status?: number };
    error.status = response.status; // 供调用方按状态码分诊（如 401 鉴权提示）
    throw error;
  }
  if (!response.body) throw new Error('响应无 body，无法读取 SSE 流');
  for await (const event of parseSseStream(response.body)) {
    onEvent(event);
  }
}

export type RunStreamStatus = 'idle' | 'streaming' | 'done' | 'cancelled' | 'error';

export interface RunStream {
  events: SseEvent[];
  status: RunStreamStatus;
  error: string | null;
  /** 非 2xx 的 HTTP 状态码（网络层失败时 null） */
  errorStatus: number | null;
  /** POST url 并开始流式消费；重复调用自动取消上一次 */
  start: (url: string, body: unknown) => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

/** Run 调试台的 SSE 流式渲染 hook：事件按序累积，cancel 立即断流 */
export function useRunStream(): RunStream {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [status, setStatus] = useState<RunStreamStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setEvents([]);
    setStatus('idle');
    setError(null);
    setErrorStatus(null);
  }, []);

  const start = useCallback(async (url: string, body: unknown) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setEvents([]);
    setError(null);
    setErrorStatus(null);
    setStatus('streaming');
    try {
      await streamSsePost(
        url,
        body,
        (event) => {
          if (!controller.signal.aborted) {
            setEvents((prev) => [...prev, event]);
          }
        },
        { signal: controller.signal },
      );
      setStatus(controller.signal.aborted ? 'cancelled' : 'done');
    } catch (cause) {
      if (controller.signal.aborted) {
        setStatus('cancelled');
        return;
      }
      setError(cause instanceof Error ? cause.message : String(cause));
      setErrorStatus((cause as Error & { status?: number }).status ?? null);
      setStatus('error');
    }
  }, []);

  return { events, status, error, errorStatus, start, cancel, reset };
}
