import {
  BookOpen,
  Bot,
  FileText,
  Gauge,
  GitBranch,
  MessageCircle,
  MessagesSquare,
  Package,
  ScrollText,
  Terminal,
  Wrench,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { NavLink } from 'react-router-dom';

import { useAgents } from '@/api/hooks';
import { cn } from '@/lib/utils';

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/prompts', label: 'Prompt 库', icon: FileText },
  { to: '/models', label: '模型', icon: Gauge },
  { to: '/tools', label: '工具', icon: Wrench },
  { to: '/skills', label: 'Skills', icon: Package },
  { to: '/knowledge', label: '知识库', icon: BookOpen },
  { to: '/graphs', label: '流程图', icon: GitBranch },
  { to: '/traces', label: 'Traces', icon: ScrollText },
  { to: '/evals', label: '评测', icon: MessagesSquare },
];

function navLinkClass({ isActive }: { isActive: boolean }) {
  return cn(
    'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors',
    isActive
      ? 'bg-accent text-accent-foreground'
      : 'text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground',
  );
}

export function Sidebar() {
  // 调试台入口指向第一个 Agent；无 Agent 时落到列表页引导新建
  const { data: agents } = useAgents();
  const debugTarget = agents?.length ? `/agents/${agents[0].key}/debug` : '/agents';

  return (
    <aside className="flex w-52 shrink-0 flex-col border-r bg-card/50">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <Terminal className="h-5 w-5 text-primary" />
        <span className="text-sm font-semibold tracking-tight">LubanAgent</span>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        <NavLink to={debugTarget} className={navLinkClass} end={false}>
          <MessagesSquare className="h-4 w-4" />
          调试台
        </NavLink>
        {/* /chat 是并列路由（不在 AppShell 内）：点击即离开布局壳进全屏聊天 */}
        <NavLink to="/chat" className={navLinkClass} end>
          <MessageCircle className="h-4 w-4" />
          用户端聊天
        </NavLink>
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} className={navLinkClass} end={item.to === '/agents'}>
            <item.icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
