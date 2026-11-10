import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import AgentDebugPage from '@/pages/agents/AgentDebugPage';
import AgentEditorPage from '@/pages/agents/AgentEditorPage';
import AgentsPage from '@/pages/agents/AgentsPage';
import ChatPage from '@/pages/chat/ChatPage';
import DiagnosticDetailPage from '@/pages/diagnostics/DiagnosticDetailPage';
import DiagnosticsPage from '@/pages/diagnostics/DiagnosticsPage';
import EvalsPage from '@/pages/evals/EvalsPage';
import GraphsPage from '@/pages/graphs/GraphsPage';
import KnowledgePage from '@/pages/knowledge/KnowledgePage';
import ModelComparePage from '@/pages/models/ModelComparePage';
import ModelsPage from '@/pages/models/ModelsPage';
import PromptsPage from '@/pages/prompts/PromptsPage';
import SecurityPage from '@/pages/security/SecurityPage';
import SkillsPage from '@/pages/skills/SkillsPage';
import ToolsPage from '@/pages/tools/ToolsPage';
import TraceDetailPage from '@/pages/traces/TraceDetailPage';
import TracesPage from '@/pages/traces/TracesPage';
import { RequirePermission } from '@/components/layout/require-permission';

/** 路由表 = 验收单位（17 条页面路由，其中 /chat 用户端聊天页为并列路由、不在 AppShell 布局壳内；D4-1 契约存档为旧清单，以此为准）；测试复用同一份 routeObjects */
export const routeObjects: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/agents" replace /> },
      {
        path: 'agents',
        element: (
          <RequirePermission permission="config:read">
            <AgentsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'agents/:key',
        element: (
          <RequirePermission permission="config:read">
            <AgentEditorPage />
          </RequirePermission>
        ),
      },
      {
        path: 'agents/:key/debug',
        element: (
          <RequirePermission permission="run:execute">
            <AgentDebugPage />
          </RequirePermission>
        ),
      },
      {
        path: 'prompts',
        element: (
          <RequirePermission permission="config:read">
            <PromptsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'models',
        element: (
          <RequirePermission permission="config:read">
            <ModelsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'models/compare',
        element: (
          <RequirePermission permission="run:execute">
            <ModelComparePage />
          </RequirePermission>
        ),
      },
      {
        path: 'tools',
        element: (
          <RequirePermission permission="config:read">
            <ToolsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'skills',
        element: (
          <RequirePermission permission="config:read">
            <SkillsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'knowledge',
        element: (
          <RequirePermission permission="config:read">
            <KnowledgePage />
          </RequirePermission>
        ),
      },
      {
        path: 'graphs',
        element: (
          <RequirePermission permission="config:read">
            <GraphsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'traces',
        element: (
          <RequirePermission permission="run:read">
            <TracesPage />
          </RequirePermission>
        ),
      },
      {
        path: 'traces/:runId',
        element: (
          <RequirePermission permission="run:read">
            <TraceDetailPage />
          </RequirePermission>
        ),
      },
      {
        path: 'evals',
        element: (
          <RequirePermission permission="eval:read">
            <EvalsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'diagnostics',
        element: (
          <RequirePermission permission="diagnostics:read">
            <DiagnosticsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'diagnostics/:runOrTraceId',
        element: (
          <RequirePermission permission="diagnostics:read">
            <DiagnosticDetailPage />
          </RequirePermission>
        ),
      },
      {
        path: 'security',
        element: (
          <RequirePermission permission="security:manage">
            <SecurityPage />
          </RequirePermission>
        ),
      },
      { path: '*', element: <Navigate to="/agents" replace /> },
    ],
  },
  // 用户端聊天页：并列路由（无侧边栏/顶栏的全屏聊天，示例级入口）
  { path: '/chat', element: <ChatPage /> },
];

export const router = createBrowserRouter(routeObjects);
