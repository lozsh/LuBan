/** H13 鉴权闭环：REST/SSE 共享身份来源 + 401 登记。 */

import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { server } from '@/mocks/node';
import { clearApiKey, setApiKey } from '@/lib/auth';

import { authedFetch, client } from './client';
import { streamSsePost } from '@/lib/sse';

afterEach(() => {
  clearApiKey();
});

describe('身份来源统一（REST / SSE / 裸 fetch）', () => {
  it('openapi-fetch 中间件：有 Key 时所有请求附 Authorization，401 登记 rejected', async () => {
    let seen: string | null = null;
    server.use(
      http.get('*/v1/agents', ({ request }) => {
        seen = request.headers.get('authorization');
        return HttpResponse.json(
          { error: { code: 'AUTH_FAILED', message: 'API Key 缺失或不匹配' } },
          { status: 401 },
        );
      }),
    );
    setApiKey('sk-live');
    const { error } = await client.GET('/v1/agents');
    expect(seen).toBe('Bearer sk-live');
    expect(error).toMatchObject({ error: { code: 'AUTH_FAILED' } });
  });

  it('authedFetch：附加 Authorization 头（上传/静态资源同源）', async () => {
    let seen: string | null = null;
    server.use(
      http.get('*/v1/agents/:key/avatar', ({ request }) => {
        seen = request.headers.get('authorization');
        return new HttpResponse('<svg/>', { headers: { 'Content-Type': 'image/svg+xml' } });
      }),
    );
    setApiKey('sk-avatar');
    const response = await authedFetch('http://localhost/v1/agents/cs-agent/avatar');
    expect(response.ok).toBe(true);
    expect(seen).toBe('Bearer sk-avatar');
  });

  it('streamSsePost：SSE 请求同样携带身份头（鉴权来源统一）', async () => {
    let seen: string | null = null;
    server.use(
      http.post('*/v1/agents/:key/runs', ({ request }) => {
        seen = request.headers.get('authorization');
        const body = `event: run.completed\ndata: {"run_id":"r1","type":"run_completed"}\n\n`;
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } });
      }),
    );
    setApiKey('sk-sse');
    await streamSsePost('http://localhost/v1/agents/cs-agent/runs', { input: 'hi' }, () => {});
    expect(seen).toBe('Bearer sk-sse');
  });

  it('Key 不进 URL query（直连契约：仅 Authorization 头承载）', async () => {
    let url = '';
    server.use(
      http.get('*/v1/runs', ({ request }) => {
        url = new URL(request.url).toString();
        return HttpResponse.json([]);
      }),
    );
    setApiKey('sk-uri');
    await client.GET('/v1/runs', { params: { query: { limit: 5 } } });
    expect(url).not.toContain('sk-uri');
    expect(url).not.toContain('api_key');
  });
});
