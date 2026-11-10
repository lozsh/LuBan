/**
 * 统一排查 API：程序日志 + Run 树 + Trace 通过同一个关联键查询。
 * 本地类型对应后端安全/排查扩展端点，等 schema codegen 后可替换。
 */

import type { Run, TraceOut } from '@/api/hooks';
import { API_BASE, authedFetch, toApiError, type ApiError } from '@/api/client';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';

export interface LogEntry {
  id: string;
  /** 对齐后端 ApplicationLogOut.created_at（ISO datetime）。 */
  created_at: string;
  /** 后端保留原始 logging level 字符串，UI 对未知级别做兜底展示。 */
  level: string;
  logger: string;
  message: string;
  /** 对齐后端 ApplicationLogOut.pathname / lineno。 */
  pathname: string;
  lineno: number;
  session_id: string | null;
  run_id: string | null;
  trace_id: string | null;
  request_id: string | null;
  parent_run_id: string | null;
  root_run_id: string | null;
  /** 后端日志里的主体（用户/账号上下文）。 */
  subject: string | null;
  exception: string | null;
  extra: Record<string, unknown>;
}

export interface DiagnosticList<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  dropped_count?: number;
  write_failures?: number;
}

export interface DiagnosticRunFilter {
  session_id?: string;
  run_id?: string;
  trace_id?: string;
  user_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface LogFilter extends DiagnosticRunFilter {
  root_run_id?: string;
  request_id?: string;
  level?: string;
  logger?: string;
  q?: string;
  from_time?: string;
  to_time?: string;
}

export interface DiagnosticDetail {
  run: Run | null;
  runs: Run[];
  traces: TraceOut[];
  log_count: number;
}

async function requestJson<T>(path: string, searchParams: URLSearchParams): Promise<T> {
  const query = searchParams.toString();
  const response = await authedFetch(`${API_BASE}${path}${query ? `?${query}` : ''}`);
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    throw toApiError(
      body ?? { error: { message: `请求失败: ${response.status}` } },
      response.status,
    );
  }
  return body as T;
}

function params(filter: object): URLSearchParams {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(
    filter as Record<string, string | number | undefined>,
  )) {
    if (value !== undefined && value !== '') result.set(key, String(value));
  }
  return result;
}

export function useLogs(
  filter: LogFilter = {},
  options: { enabled?: boolean } = {},
): UseQueryResult<DiagnosticList<LogEntry>, ApiError> {
  return useQuery({
    queryKey: ['diagnostics', 'logs', filter],
    queryFn: () => requestJson<DiagnosticList<LogEntry>>('/v1/logs', params(filter)),
    enabled: options.enabled,
  });
}

export function useDiagnosticRuns(
  filter: DiagnosticRunFilter = {},
): UseQueryResult<DiagnosticList<Run>, ApiError> {
  return useQuery({
    queryKey: ['diagnostics', 'runs', filter],
    queryFn: () => requestJson<DiagnosticList<Run>>('/v1/diagnostics/runs', params(filter)),
  });
}

export function useDiagnosticDetail(id: string): UseQueryResult<DiagnosticDetail, ApiError> {
  return useQuery({
    queryKey: ['diagnostics', 'detail', id],
    queryFn: () =>
      requestJson<DiagnosticDetail>(
        `/v1/diagnostics/runs/${encodeURIComponent(id)}`,
        new URLSearchParams(),
      ),
    enabled: id !== '',
  });
}
