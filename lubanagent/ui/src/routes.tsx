import { createBrowserRouter, Navigate, type RouteObject } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import AgentDebugPage from '@/pages/agents/AgentDebugPage';
import AgentEditorPage from '@/pages/agents/AgentEditorPage';
import AgentsPage from '@/pages/agents/AgentsPage';
import ChatPage from '@/pages/chat/ChatPage';
import EvalsPage from '@/pages/evals/EvalsPage';
import GraphsPage from '@/pages/graphs/GraphsPage';
import KnowledgePage from '@/pages/knowledge/KnowledgePage';
import ModelComparePage from '@/pages/models/ModelComparePage';
import ModelsPage from '@/pages/models/ModelsPage';
import PromptsPage from '@/pages/prompts/PromptsPage';
import SkillsPage from '@/pages/skills/SkillsPage';
import ToolsPage from '@/pages/tools/ToolsPage';
import TraceDetailPage from '@/pages/traces/TraceDetailPage';
import TracesPage from '@/pages/traces/TracesPage';

/** 路由表 = 验收单位（14 条页面路由，其中 /chat 用户端聊天页为并列路由、不在 AppShell 布局壳内；D4-1 契约存档为旧清单，以此为准）；测试复用同一份 routeObjects */
export const routeObjects: RouteObject[] = [
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/agents" replace /> },
      { path: 'agents', element: <AgentsPage /> },
      { path: 'agents/:key', element: <AgentEditorPage /> },
      { path: 'agents/:key/debug', element: <AgentDebugPage /> },
      { path: 'prompts', element: <PromptsPage /> },
      { path: 'models', element: <ModelsPage /> },
      { path: 'models/compare', element: <ModelComparePage /> },
      { path: 'tools', element: <ToolsPage /> },
      { path: 'skills', element: <SkillsPage /> },
      { path: 'knowledge', element: <KnowledgePage /> },
      { path: 'graphs', element: <GraphsPage /> },
      { path: 'traces', element: <TracesPage /> },
      { path: 'traces/:runId', element: <TraceDetailPage /> },
      { path: 'evals', element: <EvalsPage /> },
      { path: '*', element: <Navigate to="/agents" replace /> },
    ],
  },
  // 用户端聊天页：并列路由（无侧边栏/顶栏的全屏聊天，示例级入口）
  { path: '/chat', element: <ChatPage /> },
];

export const router = createBrowserRouter(routeObjects);
