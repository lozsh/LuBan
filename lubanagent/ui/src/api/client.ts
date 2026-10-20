/**
 * openapi-fetch 封装：baseURL 读 VITE_API_BASE（缺省空串 = 同源，dev 走 vite 代理 / MSW 拦截）。
 * 统一错误：后端 ErrorOut 形状 { error: { code, message, details, retryable } } → ApiError。
 * 鉴权（H13）：中间件对全部 /v1 请求附加 Authorization: Bearer <key>（内存态，
 * 见 lib/auth.ts）；401 统一 markUnauthorized（不自动重试），由顶栏鉴权提示引导恢复。
 */

import createClient from 'openapi-fetch';

import { authHeaders, markUnauthorized } from '@/lib/auth';

import type { paths } from './schema';

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '';

export const client = createClient<paths>({ baseUrl: API_BASE });

// REST 统一身份注入：每个 /v1 请求带上内存态 Key；401 统一登记（不重试）
client.use({
  onRequest({ request }) {
    for (const [name, value] of Object.entries(authHeaders())) {
      request.headers.set(name, value);
    }
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401) markUnauthorized();
    return response;
  },
});

/**
 * 裸 fetch 共用身份来源（SSE/上传等不经 openapi-fetch 的请求，H13 契约）：
 * 合并内存态 Authorization 头；401 统一 markUnauthorized。调用方仍自行处理非 2xx。
 */
export async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  for (const [name, value] of Object.entries(authHeaders())) {
    headers.set(name, value);
  }
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) markUnauthorized();
  return response;
}

/** 后端统一错误体（api/errors.py 的 ErrorOut；由异常处理器产出，不进 OpenAPI components） */
export interface ErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  retryable?: boolean;
}

/** 后端统一错误响应形状：{ "error": ErrorBody } */
export interface ErrorOut {
  error: ErrorBody;
}

export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly retryable: boolean;
  readonly status?: number;

  constructor(body: Partial<ErrorBody> & { message?: string }, status?: number) {
    super(body.message ?? `请求失败${status ? `: ${status}` : ''}`);
    this.name = 'ApiError';
    this.code = body.code ?? 'UNKNOWN';
    this.details = body.details ?? {};
    this.retryable = body.retryable ?? false;
    this.status = status;
  }
}

/** openapi-fetch 返回的 error 值（ErrorOut | 校验错误）→ ApiError；queryFn/mutationFn 统一经此抛出 */
export function toApiError(error: unknown, status?: number): ApiError {
  if (error instanceof ApiError) return error;
  if (error && typeof error === 'object' && 'error' in error) {
    const body = (error as { error: Partial<ErrorBody> }).error;
    return new ApiError(body ?? {}, status);
  }
  if (error instanceof Error) return new ApiError({ message: error.message }, status);
  return new ApiError({ message: String(error) }, status);
}
