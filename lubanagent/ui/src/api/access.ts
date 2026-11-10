/**
 * 访问控制 API：账号登录、当前身份、用户/角色管理。
 * schema.d.ts 尚未包含安全模块，这里先维护本地契约类型；codegen 后可收敛到 generated types。
 */

import { API_BASE, ApiError, authedFetch, toApiError } from '@/api/client';
import type { SessionUser } from '@/lib/auth';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

export type { SessionUser };

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: SessionUser;
}

export interface UserOut {
  id: string;
  username: string;
  display_name: string;
  enabled: boolean;
  /** 后端 UserPublic 的 bootstrap 标记；仅用于管理界面展示，不用于前端授权。 */
  bootstrap?: boolean;
  role_keys: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface UserUpsert {
  username: string;
  display_name?: string;
  enabled?: boolean;
  role_keys?: string[];
  password?: string;
}

export interface UserPatch {
  display_name?: string;
  enabled?: boolean;
  role_keys?: string[];
  password?: string;
}

export interface RoleOut {
  key: string;
  name: string;
  permissions: string[];
  protected?: boolean;
}

export interface RoleUpsert {
  key: string;
  name: string;
  permissions: string[];
}

export interface PermissionOut {
  key: string;
  name: string;
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await authedFetch(`${API_BASE}${path}`, init);
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    throw toApiError(
      body ?? { error: { message: `请求失败: ${response.status}` } },
      response.status,
    );
  }
  return body as T;
}

/** 未登录返回 null 而不是错误：403/401 页面需要区分“未登录”和“权限不足”。 */
export async function fetchCurrentUser(): Promise<SessionUser | null> {
  const response = await authedFetch(`${API_BASE}/v1/auth/me`);
  if (response.status === 401) return null;
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    throw toApiError(
      body ?? { error: { message: `读取身份失败: ${response.status}` } },
      response.status,
    );
  }
  return body as SessionUser;
}

export function useMe(): UseQueryResult<SessionUser | null, ApiError> {
  return useQuery({ queryKey: ['access', 'me'], queryFn: fetchCurrentUser, retry: false });
}

export function useLogin(): UseMutationResult<
  LoginResponse,
  ApiError,
  { username: string; password: string }
> {
  return useMutation({
    mutationFn: async (body) => {
      // 登录是公开端点；不用 authedFetch 是为了把“密码错误”和“当前凭据过期”分开，
      // 避免输错密码时全局 markUnauthorized 清掉正在查看的私有缓存。
      const response = await fetch(`${API_BASE}/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const responseBody = (await response.json().catch(() => null)) as unknown;
      if (!response.ok) {
        const errorBody = (responseBody as { error?: { message?: string; code?: string } } | null)
          ?.error ?? { message: `登录失败: ${response.status}` };
        throw new ApiError(errorBody, response.status);
      }
      return responseBody as LoginResponse;
    },
  });
}

export function useLogout(): UseMutationResult<{ logged_out: boolean }, ApiError, void> {
  return useMutation({
    mutationFn: () => requestJson<{ logged_out: boolean }>('/v1/auth/logout', { method: 'POST' }),
  });
}

export function useUsers(): UseQueryResult<UserOut[], ApiError> {
  return useQuery({
    queryKey: ['security', 'users'],
    queryFn: () => requestJson<UserOut[]>('/v1/security/users'),
  });
}

export function useCreateUser(): UseMutationResult<UserOut, ApiError, UserUpsert> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      requestJson<UserOut>('/v1/security/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['security', 'users'] }),
  });
}

export function useUpdateUser(): UseMutationResult<
  UserOut,
  ApiError,
  { id: string; patch: UserPatch }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }) =>
      requestJson<UserOut>(`/v1/security/users/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['security', 'users'] });
      // 当前账号被改角色/启用状态时，顶栏和权限闸门必须立即拿到新 permissions。
      queryClient.invalidateQueries({ queryKey: ['access', 'me'] });
    },
  });
}

export function useRoles(): UseQueryResult<RoleOut[], ApiError> {
  return useQuery({
    queryKey: ['security', 'roles'],
    queryFn: () => requestJson<RoleOut[]>('/v1/security/roles'),
  });
}

export function useCreateRole(): UseMutationResult<RoleOut, ApiError, RoleUpsert> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      requestJson<RoleOut>('/v1/security/roles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['security', 'roles'] });
      queryClient.invalidateQueries({ queryKey: ['access', 'me'] });
    },
  });
}

export function useUpdateRole(): UseMutationResult<
  RoleOut,
  ApiError,
  { key: string; name: string; permissions: string[] }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      requestJson<RoleOut>(`/v1/security/roles/${encodeURIComponent(body.key)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['security', 'roles'] });
      // 当前账号权限可能来自刚变更的角色，立即刷新 me；旧权限不再作为 UI 判定依据。
      queryClient.invalidateQueries({ queryKey: ['access', 'me'] });
    },
  });
}

export function useDeleteRole(): UseMutationResult<{ deleted: boolean }, ApiError, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (key) =>
      requestJson<{ deleted: boolean }>(`/v1/security/roles/${encodeURIComponent(key)}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['security', 'roles'] });
      queryClient.invalidateQueries({ queryKey: ['security', 'users'] });
      queryClient.invalidateQueries({ queryKey: ['access', 'me'] });
    },
  });
}

export function usePermissions(): UseQueryResult<PermissionOut[], ApiError> {
  return useQuery({
    queryKey: ['security', 'permissions'],
    queryFn: () => requestJson<PermissionOut[]>('/v1/security/permissions'),
  });
}
