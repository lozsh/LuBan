import { Outlet } from 'react-router-dom';

import { Sidebar } from './sidebar';
import { Topbar } from './topbar';

/** 布局壳：左侧导航 + 顶栏（环境徽章 / API 连通状态 / 主题切换） */
export function AppShell() {
  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
