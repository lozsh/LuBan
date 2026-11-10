/** 路由级权限闸门：未登录引导登录，已登录但无权限显示 403。 */

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { usePermissions } from '@/lib/permissions';

export function RequirePermission({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { me, isLoading, error, can } = usePermissions();

  if (isLoading) {
    return <p className="p-6 text-sm text-muted-foreground">正在读取访问权限…</p>;
  }

  if (error) {
    return (
      <div className="p-6">
        <Card className="border-red-500/40">
          <CardHeader>
            <CardTitle className="text-xl">权限读取失败</CardTitle>
            <CardDescription>{error.message}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-xl">请先登录</CardTitle>
            <CardDescription>访问该页面需要账号身份。请点击右上角登录后再试。</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild>
              <Link to="/agents">返回工作台</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!can(permission)) {
    return (
      <div className="p-6">
        <Card className="border-red-500/40" data-testid="forbidden-page">
          <CardHeader>
            <CardTitle className="text-xl">403 权限不足</CardTitle>
            <CardDescription>
              当前账号「{me.display_name}」缺少 <span className="font-mono">{permission}</span>
              权限。请联系管理员调整角色。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline">
              <Link to="/agents">返回工作台</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return <>{children}</>;
}
