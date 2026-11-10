/** 用户与角色管理：账号启用、角色分配、权限点编辑；受保护角色由后端兜底。 */

import { useState } from 'react';

import {
  useCreateRole,
  useCreateUser,
  useDeleteRole,
  usePermissions as usePermissionCatalog,
  useRoles,
  useUpdateRole,
  useUpdateUser,
  useUsers,
  type RoleUpsert,
} from '@/api/access';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { usePermissions } from '@/lib/permissions';
import { cn } from '@/lib/utils';

function Checkbox({
  id,
  checked,
  disabled,
  onCheckedChange,
  label,
}: {
  id: string;
  checked: boolean;
  disabled?: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm">
      <input
        id={id}
        type="checkbox"
        className="h-4 w-4 accent-primary"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onCheckedChange(event.target.checked)}
      />
      <span className={cn(disabled && 'text-muted-foreground')}>{label}</span>
    </label>
  );
}

export default function SecurityPage() {
  const usersQuery = useUsers();
  const rolesQuery = useRoles();
  const permissionCatalog = usePermissionCatalog();
  const { me } = usePermissions();
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const createRole = useCreateRole();
  const updateRole = useUpdateRole();
  const deleteRole = useDeleteRole();

  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [userRoleKeys, setUserRoleKeys] = useState<string[]>([]);

  const [roleKey, setRoleKey] = useState('');
  const [roleName, setRoleName] = useState('');
  const [rolePermissions, setRolePermissions] = useState<string[]>([]);
  const [passwordTarget, setPasswordTarget] = useState<{ id: string; username: string } | null>(
    null,
  );
  const [resetPassword, setResetPassword] = useState('');

  const mutationError = [
    createUser.error,
    updateUser.error,
    createRole.error,
    updateRole.error,
    deleteRole.error,
  ].find((error) => error !== null);

  const queryError = [usersQuery.error, rolesQuery.error, permissionCatalog.error].find(
    (error) => error !== null,
  );
  const queryLoading = usersQuery.isLoading || rolesQuery.isLoading || permissionCatalog.isLoading;

  function toggleValue(list: string[], value: string): string[] {
    return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
  }

  async function submitUser(): Promise<void> {
    if (!username.trim() || password === '') return;
    try {
      await createUser.mutateAsync({
        username: username.trim(),
        display_name: displayName.trim() || username.trim(),
        password,
        role_keys: userRoleKeys,
        enabled: true,
      });
      setUsername('');
      setDisplayName('');
      setPassword('');
      setUserRoleKeys([]);
    } catch {
      // mutation.error 已统一展示，这里只阻止 Promise 冒泡成 unhandled rejection。
    }
  }

  async function submitRole(): Promise<void> {
    if (!roleKey.trim()) return;
    const body: RoleUpsert = {
      key: roleKey.trim(),
      name: roleName.trim() || roleKey.trim(),
      permissions: rolePermissions,
    };
    try {
      await createRole.mutateAsync(body);
      setRoleKey('');
      setRoleName('');
      setRolePermissions([]);
    } catch {
      // 同上：错误状态由 mutation 对象保留。
    }
  }

  async function submitResetPassword(): Promise<void> {
    if (!passwordTarget || resetPassword === '') return;
    try {
      await updateUser.mutateAsync({
        id: passwordTarget.id,
        patch: { password: resetPassword },
      });
      setPasswordTarget(null);
      setResetPassword('');
    } catch {
      // 全局 mutationError 展示后端校验/权限错误。
    }
  }

  return (
    <div className="space-y-5 p-4">
      <header>
        <h1 className="text-xl font-semibold">用户与角色</h1>
        <p className="text-sm text-muted-foreground">
          账号绑定角色，角色绑定权限点。前端只隐藏入口；真正的越权请求仍由后端拒绝。
        </p>
      </header>

      {mutationError && (
        <div className="rounded border border-red-500/50 p-3 text-sm text-red-500">
          操作失败：{mutationError.message}
        </div>
      )}

      {queryError && (
        <div className="rounded border border-red-500/50 p-3 text-sm text-red-500">
          数据读取失败：{queryError.message}
        </div>
      )}
      {queryLoading && <p className="text-sm text-muted-foreground">正在读取用户与角色…</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">用户（{usersQuery.data?.length ?? 0}）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>账号</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead>角色</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(usersQuery.data ?? []).map((user) => (
                    <TableRow key={user.id} data-testid={`security-user-${user.username}`}>
                      <TableCell>
                        <div className="font-medium">{user.display_name}</div>
                        <div className="font-mono text-xs text-muted-foreground">
                          {user.username}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant={user.enabled ? 'default' : 'outline'}
                          disabled={
                            me?.username === user.username ||
                            (updateUser.isPending && updateUser.variables?.id === user.id)
                          }
                          onClick={() =>
                            updateUser.mutate({
                              id: user.id,
                              patch: { enabled: !user.enabled },
                            })
                          }
                        >
                          {user.enabled ? '已启用' : '已禁用'}
                        </Button>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-2">
                          {(rolesQuery.data ?? []).map((role) => (
                            <Checkbox
                              key={role.key}
                              id={`user-${user.id}-role-${role.key}`}
                              label={role.name}
                              checked={user.role_keys.includes(role.key)}
                              disabled={
                                updateUser.isPending && updateUser.variables?.id === user.id
                              }
                              onCheckedChange={(checked) =>
                                updateUser.mutate({
                                  id: user.id,
                                  patch: {
                                    role_keys: checked
                                      ? [...user.role_keys, role.key]
                                      : user.role_keys.filter((key) => key !== role.key),
                                  },
                                })
                              }
                            />
                          ))}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            setPasswordTarget({ id: user.id, username: user.username })
                          }
                        >
                          重置密码
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            <form
              className="grid gap-3 rounded border p-3 md:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault();
                void submitUser();
              }}
            >
              <div className="space-y-1">
                <Label htmlFor="new-username">用户名</Label>
                <Input
                  id="new-username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-display-name">显示名</Label>
                <Input
                  id="new-display-name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-password">初始密码</Label>
                <Input
                  id="new-password"
                  type="password"
                  minLength={12}
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label>初始角色</Label>
                <div className="flex flex-wrap gap-3">
                  {(rolesQuery.data ?? []).map((role) => (
                    <Checkbox
                      key={role.key}
                      id={`new-user-role-${role.key}`}
                      label={role.name}
                      checked={userRoleKeys.includes(role.key)}
                      onCheckedChange={() => setUserRoleKeys((prev) => toggleValue(prev, role.key))}
                    />
                  ))}
                </div>
              </div>
              <div className="md:col-span-2">
                <Button type="submit" disabled={createUser.isPending}>
                  新建用户
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">角色与权限</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-3">
              {(rolesQuery.data ?? []).map((role) => (
                <div
                  key={role.key}
                  className="rounded border p-3"
                  data-testid={`security-role-${role.key}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <div className="text-sm font-medium">{role.name}</div>
                      <div className="font-mono text-xs text-muted-foreground">{role.key}</div>
                    </div>
                    {!role.protected && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={deleteRole.isPending && deleteRole.variables === role.key}
                        onClick={() => deleteRole.mutate(role.key)}
                      >
                        删除
                      </Button>
                    )}
                  </div>
                  <div className="mt-2 grid gap-2">
                    {(permissionCatalog.data ?? []).map((permission) => (
                      <Checkbox
                        key={permission.key}
                        id={`role-${role.key}-${permission.key}`}
                        label={`${permission.name}（${permission.key}）`}
                        checked={
                          role.protected ||
                          role.permissions.includes(permission.key) ||
                          role.permissions.includes('*')
                        }
                        disabled={
                          role.protected ||
                          (updateRole.isPending && updateRole.variables?.key === role.key)
                        }
                        onCheckedChange={(checked) =>
                          updateRole.mutate({
                            key: role.key,
                            name: role.name,
                            permissions: checked
                              ? [...role.permissions, permission.key]
                              : role.permissions.filter((key) => key !== permission.key),
                          })
                        }
                      />
                    ))}
                  </div>
                  {role.protected && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      受保护角色：admin 保持全量权限。
                    </p>
                  )}
                </div>
              ))}
            </div>

            <form
              className="space-y-3 rounded border p-3"
              onSubmit={(event) => {
                event.preventDefault();
                void submitRole();
              }}
            >
              <div className="space-y-1">
                <Label htmlFor="new-role-key">角色 key</Label>
                <Input
                  id="new-role-key"
                  value={roleKey}
                  onChange={(event) => setRoleKey(event.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="new-role-name">角色名称</Label>
                <Input
                  id="new-role-name"
                  value={roleName}
                  onChange={(event) => setRoleName(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>权限点</Label>
                <div className="grid gap-2">
                  {(permissionCatalog.data ?? []).map((permission) => (
                    <Checkbox
                      key={permission.key}
                      id={`new-role-${permission.key}`}
                      label={permission.name}
                      checked={rolePermissions.includes(permission.key)}
                      onCheckedChange={() =>
                        setRolePermissions((prev) => toggleValue(prev, permission.key))
                      }
                    />
                  ))}
                </div>
              </div>
              <Button type="submit" disabled={createRole.isPending}>
                新建角色
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>

      <Dialog
        open={passwordTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPasswordTarget(null);
            setResetPassword('');
          }
        }}
      >
        <DialogContent data-testid="reset-password-dialog">
          <DialogHeader>
            <DialogTitle>重置密码</DialogTitle>
            <DialogDescription>
              为 <span className="font-mono">{passwordTarget?.username}</span> 设置新密码；密码长度
              12–256 位，提交后立即生效。
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              void submitResetPassword();
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="reset-password">新密码</Label>
              <Input
                id="reset-password"
                type="password"
                minLength={12}
                maxLength={256}
                autoComplete="new-password"
                value={resetPassword}
                onChange={(event) => setResetPassword(event.target.value)}
                data-testid="reset-password-input"
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setPasswordTarget(null);
                  setResetPassword('');
                }}
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={updateUser.isPending}
                data-testid="reset-password-submit"
              >
                保存新密码
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
