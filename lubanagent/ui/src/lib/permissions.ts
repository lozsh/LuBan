/** 权限判定统一入口：只信任 /v1/auth/me 返回的 permissions，不在前端推断角色。 */

import { useMe } from '@/api/access';
import type { SessionUser } from '@/lib/auth';

export function hasPermission(user: SessionUser | null | undefined, permission: string): boolean {
  if (!user) return false;
  return user.permissions.includes('*') || user.permissions.includes(permission);
}

export function usePermissions() {
  const query = useMe();
  return {
    me: query.data ?? null,
    isLoading: query.isLoading,
    error: query.error,
    can: (permission: string) => hasPermission(query.data, permission),
  };
}
