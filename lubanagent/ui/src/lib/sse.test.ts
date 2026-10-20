import { renderHook, act, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { parseSseFrame, parseSseStream, useRunStream } from './sse';

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events = [];
  for await (const event of parseSseStream(stream)) events.push(event);
  return events;
}

describe('parseSseFrame', () => {
  it('解析 event + data，data 尽力 JSON.parse', () => {
    const frame = parseSseFrame('event: run.started\ndata: {"run_id":"r1","type":"run_started"}');
    expect(frame).toEqual({
      event: 'run.started',
      data: { run_id: 'r1', type: 'run_started' },
      raw: '{"run_id":"r1","type":"run_started"}',
    });
  });

  it('无 event 行默认 message；多行 data 用换行拼接', () => {
    const frame = parseSseFrame('data: 第一行\ndata: 第二行');
    expect(frame?.event).toBe('message');
    expect(frame?.data).toBe('第一行\n第二行');
  });

  it('忽略注释/心跳行；无 data 返回 null', () => {
    expect(parseSseFrame(': ping\n\n')).toBeNull();
    expect(parseSseFrame('event: run.started')).toBeNull();
  });

  it('非 JSON data 保留原文', () => {
    expect(parseSseFrame('data: 纯文本')?.data).toBe('纯文本');
  });
});

describe('parseSseStream', () => {
  it('跨 chunk 缓冲拼帧', async () => {
    const events = await collect(
      streamOf([
        'event: run.started\nda',
        'ta: {"run_id":"r1"}\n\nevent: token.delta\ndata: {"text":"你好"}',
        '\n\nevent: run.completed\ndata: {"end_reason":"final_answer"}\n\n',
      ]),
    );
    expect(events.map((event) => event.event)).toEqual([
      'run.started',
      'token.delta',
      'run.completed',
    ]);
    expect(events[1].data).toEqual({ text: '你好' });
  });

  it('兼容 CRLF', async () => {
    const events = await collect(streamOf(['event: a\r\ndata: 1\r\n\r\n']));
    expect(events).toHaveLength(1);
  });

  it('流末尾无空行也能产出最后一帧', async () => {
    const events = await collect(streamOf(['event: tail\ndata: {"x":1}']));
    expect(events.map((event) => event.event)).toEqual(['tail']);
  });
});

describe('useRunStream', () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubSseFetch(chunks: string[]) {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(streamOf(chunks), {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          }),
      ),
    );
  }

  it('完整流：事件按序累积，终态 done', async () => {
    stubSseFetch([
      'event: run.started\ndata: {"run_id":"r1"}\n\n',
      'event: token.delta\ndata: {"text":"你好"}\n\n',
      'event: run.completed\ndata: {"end_reason":"final_answer"}\n\n',
    ]);
    const { result } = renderHook(() => useRunStream());
    await act(async () => {
      await result.current.start('http://localhost/v1/agents/cs-agent/runs', { input: '你好' });
    });
    expect(result.current.status).toBe('done');
    expect(result.current.events.map((event) => event.event)).toEqual([
      'run.started',
      'token.delta',
      'run.completed',
    ]);
    expect(result.current.error).toBeNull();
  });

  it('非 2xx：解析 ErrorOut 形状进 error，终态 error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ error: { code: 'AGENT_NOT_FOUND', message: 'Agent 不存在: ghost' } }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );
    const { result } = renderHook(() => useRunStream());
    await act(async () => {
      await result.current.start('http://localhost/v1/agents/ghost/runs', { input: 'hi' });
    });
    expect(result.current.status).toBe('error');
    expect(result.current.error).toBe('Agent 不存在: ghost');
    expect(result.current.events).toEqual([]);
  });

  it('cancel 断流，终态 cancelled', async () => {
    const encoder = new TextEncoder();
    let closed = false;
    const slowStream = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(encoder.encode('event: run.started\ndata: {"run_id":"r1"}\n\n'));
        // 持续吐帧直到被取消
        for (let i = 0; i < 100 && !closed; i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 5));
          try {
            controller.enqueue(encoder.encode(`event: token.delta\ndata: {"text":"${i}"}\n\n`));
          } catch {
            break;
          }
        }
        try {
          controller.close();
        } catch {
          // 已取消
        }
      },
      cancel() {
        closed = true;
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(slowStream, { status: 200 })),
    );
    const { result } = renderHook(() => useRunStream());
    let promise: Promise<void> = Promise.resolve();
    act(() => {
      promise = result.current.start('http://localhost/v1/agents/cs-agent/runs', { input: 'x' });
    });
    await waitFor(() => expect(result.current.events.length).toBeGreaterThan(0));
    act(() => result.current.cancel());
    await act(async () => {
      await promise;
    });
    expect(result.current.status).toBe('cancelled');
  });
});
