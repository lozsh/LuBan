/**
 * react-query hooks 骨架：服务端状态一律走这里（契约 D4-1 决策 6）。
 * queryFn 统一 unwrap {data, error}，错误经 toApiError 归一。
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import { authedFetch, client, toApiError, type ApiError } from './client';
import type { components } from './schema';

export type AgentDefinition = components['schemas']['AgentDefinition'];
export type AgentVersion = components['schemas']['AgentVersion'];
export type ModelDefinition = components['schemas']['ModelDefinition'];
export type ModelDefinitionOut = components['schemas']['ModelDefinitionOut'];
export type BrainMappingOut = components['schemas']['BrainMappingOut'];
export type BrainMappingSetRequest = components['schemas']['BrainMappingSetRequest'];
export type BrainProfile = components['schemas']['BrainProfile'];
export type ToolDescriptor = components['schemas']['ToolDescriptor'];
export type ToolResult = components['schemas']['ToolResult'];
export type SkillOut = components['schemas']['SkillOut'];
export type PromptDefinition = components['schemas']['PromptDefinition'];
export type PromptVersion = components['schemas']['PromptVersion'];
export type Run = components['schemas']['Run'];
export type RunCreateRequest = components['schemas']['RunCreateRequest'];
export type TraceOut = components['schemas']['TraceOut'];
export type ApprovalOut = components['schemas']['ApprovalOut'];
export type DecideRequest = components['schemas']['DecideRequest'];
export type DecideOut = components['schemas']['DecideOut'];
export type EvalReportSummary = components['schemas']['EvalReportSummary'];
export type EvalReportDetail = components['schemas']['EvalReportDetail'];
export type EvalCaseResult = components['schemas']['CaseResult'];
export type EvalAssertionVerdict = components['schemas']['AssertionVerdict'];
export type KnowledgeSourceOut = components['schemas']['KnowledgeSourceOut'];
export type KnowledgeFileOut = components['schemas']['KnowledgeFileOut'];
export type KnowledgeChunkOut = components['schemas']['KnowledgeChunkOut'];
export type KnowledgeHitOut = components['schemas']['KnowledgeHitOut'];
export type KnowledgeRechunkRequest = components['schemas']['KnowledgeRechunkRequest'];
export type KnowledgeQueryRequest = components['schemas']['KnowledgeQueryRequest'];

async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise;
  if (error !== undefined && error !== null) throw toApiError(error, response.status);
  if (data === undefined) throw toApiError(new Error(`响应无数据: ${response.status}`));
  return data;
}

// ---- 连通性（顶栏 API 状态徽章；/health 不在 /v1 前缀内） ----
export function useHealth(): UseQueryResult<{ status: string }, ApiError> {
  return useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const response = await fetch(`${clientBase()}/health`);
      if (!response.ok) throw toApiError(new Error(`health ${response.status}`), response.status);
      return (await response.json()) as { status: string };
    },
    refetchInterval: 10_000,
    retry: false,
  });
}

function clientBase(): string {
  // 与 client.baseUrl 同源：空串 = 相对路径（dev 代理 / MSW / 生产同源托管）
  return import.meta.env.VITE_API_BASE ?? '';
}

// ---- Agents ----
export function useAgents(): UseQueryResult<AgentDefinition[], ApiError> {
  return useQuery({ queryKey: ['agents'], queryFn: () => unwrap(client.GET('/v1/agents')) });
}

export function useAgent(key: string): UseQueryResult<AgentDefinition, ApiError> {
  return useQuery({
    queryKey: ['agents', key],
    queryFn: () => unwrap(client.GET('/v1/agents/{key}', { params: { path: { key } } })),
    enabled: key !== '',
  });
}

export function useAgentVersion(
  key: string,
  version: number,
): UseQueryResult<AgentVersion, ApiError> {
  return useQuery({
    queryKey: ['agents', key, 'versions', version],
    queryFn: () =>
      unwrap(
        client.GET('/v1/agents/{key}/versions/{version}', { params: { path: { key, version } } }),
      ),
    enabled: key !== '' && version > 0,
  });
}

export function useCreateAgent(): UseMutationResult<AgentDefinition, ApiError, AgentDefinition> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition) => unwrap(client.POST('/v1/agents', { body: definition })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agents'] }),
  });
}

/** 保存 = 新建不可变版本（definition.version 递增由调用方负责） */
export function useCreateAgentVersion(
  key: string,
): UseMutationResult<AgentVersion, ApiError, AgentDefinition> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition) =>
      unwrap(
        client.POST('/v1/agents/{key}/versions', { params: { path: { key } }, body: definition }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      queryClient.invalidateQueries({ queryKey: ['agents', key] });
    },
  });
}

// ---- Avatar（Agent 虚拟形象：上传即版本，multipart 直接 fetch） ----

export function useUploadAvatar(): UseMutationResult<
  AgentDefinition,
  ApiError,
  { key: string; file: File }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ key, file }) => {
      const form = new FormData();
      form.append('file', file);
      const response = await authedFetch(
        `${clientBase()}/v1/agents/${encodeURIComponent(key)}/avatar`,
        { method: 'PUT', body: form },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw toApiError(
          body?.error ?? { message: `上传失败: ${response.status}` },
          response.status,
        );
      }
      return (await response.json()) as AgentDefinition;
    },
    onSuccess: (definition) => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      queryClient.invalidateQueries({ queryKey: ['agents', definition.key] });
    },
  });
}

export function useRemoveAvatar(): UseMutationResult<
  { removed: boolean },
  ApiError,
  { key: string }
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key }) =>
      unwrap(client.DELETE('/v1/agents/{key}/avatar', { params: { path: { key } } })),
    onSuccess: (_, { key }) => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      queryClient.invalidateQueries({ queryKey: ['agents', key] });
    },
  });
}

// ---- Runs（SSE 创建走 useRunStream，不经 react-query） ----
export interface RunsFilter {
  agent_key?: string;
  session_id?: string;
  user_id?: string;
  limit?: number;
}

export function useRuns(filter: RunsFilter = {}): UseQueryResult<Run[], ApiError> {
  return useQuery({
    queryKey: ['runs', filter],
    queryFn: () =>
      unwrap(
        client.GET('/v1/runs', {
          params: {
            query: {
              agent_key: filter.agent_key,
              session_id: filter.session_id,
              user_id: filter.user_id,
              limit: filter.limit ?? 50,
            },
          },
        }),
      ),
  });
}

export function useRun(runId: string): UseQueryResult<Run, ApiError> {
  return useQuery({
    queryKey: ['runs', runId],
    queryFn: () => unwrap(client.GET('/v1/runs/{run_id}', { params: { path: { run_id: runId } } })),
    enabled: runId !== '',
  });
}

/** 取消返回 {cancelled: bool}（schema 为开放字典形状） */
export function useCancelRun(): UseMutationResult<{ [key: string]: boolean }, ApiError, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId) =>
      unwrap(client.POST('/v1/runs/{run_id}/cancel', { params: { path: { run_id: runId } } })),
    onSuccess: (_data, runId) => {
      queryClient.invalidateQueries({ queryKey: ['runs'] });
      queryClient.invalidateQueries({ queryKey: ['runs', runId] });
    },
  });
}

// ---- 模型中心 ----
export function useModels(): UseQueryResult<ModelDefinitionOut[], ApiError> {
  return useQuery({ queryKey: ['models'], queryFn: () => unwrap(client.GET('/v1/models')) });
}

export function useUpsertModel(): UseMutationResult<ModelDefinitionOut, ApiError, ModelDefinition> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition) => unwrap(client.PUT('/v1/models', { body: definition })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['models'] }),
  });
}

export function useSetBrainMapping(): UseMutationResult<
  BrainMappingOut,
  ApiError,
  BrainMappingSetRequest
> {
  return useMutation({
    mutationFn: (body) => unwrap(client.PUT('/v1/brain-mappings', { body })),
  });
}

// ---- Tool 中心 ----
export function useTools(): UseQueryResult<ToolDescriptor[], ApiError> {
  return useQuery({ queryKey: ['tools'], queryFn: () => unwrap(client.GET('/v1/tools')) });
}

export interface ToolTestVariables {
  key: string;
  args: Record<string, unknown>;
}

export function useTestTool(): UseMutationResult<ToolResult, ApiError, ToolTestVariables> {
  return useMutation({
    mutationFn: ({ key, args }) =>
      unwrap(client.POST('/v1/tools/{key}/test', { params: { path: { key } }, body: args })),
  });
}

// ---- Skills ----
export function useSkills(): UseQueryResult<SkillOut[], ApiError> {
  return useQuery({ queryKey: ['skills'], queryFn: () => unwrap(client.GET('/v1/skills')) });
}

/** 上传 Skill zip 包（multipart/form-data；不经 openapi-fetch，直接 fetch 复用 clientBase） */
export function useUploadSkill(): UseMutationResult<SkillOut, ApiError, File> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file) => {
      const form = new FormData();
      form.append('file', file);
      const response = await authedFetch(`${clientBase()}/v1/skills`, {
        method: 'POST',
        body: form,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw toApiError(
          body?.error ?? { message: `上传失败: ${response.status}` },
          response.status,
        );
      }
      return (await response.json()) as SkillOut;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['skills'] }),
  });
}

/** 删除 Skill 包（移除目录 + scan 刷新） */
export function useDeleteSkill(): UseMutationResult<void, ApiError, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (key) => {
      const response = await authedFetch(
        `${clientBase()}/v1/skills/${encodeURIComponent(key)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw toApiError(
          body?.error ?? { message: `删除失败: ${response.status}` },
          response.status,
        );
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['skills'] }),
  });
}

export interface AssignSkillArgs {
  skillKey: string;
  /** agentKey → 期望分配状态（true=分配；缺省=不分配） */
  targets: Record<string, boolean>;
  /** 当前 Agent 全量定义（算新版本用）；仅对状态变化的 Agent 建版本 */
  agents: AgentDefinition[];
}

/** 分配 Skill 到多个 Agent：对状态变化的 Agent 各 POST 一个新版本（skills ± skillKey） */
export function useAssignSkillToAgents(): UseMutationResult<void, ApiError, AssignSkillArgs> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ skillKey, targets, agents }) => {
      await Promise.all(
        agents.map(async (a) => {
          const wasAssigned = (a.skills ?? []).includes(skillKey);
          const shouldBe = targets[a.key] === true;
          if (wasAssigned === shouldBe) return; // 无变化跳过
          const newSkills = shouldBe
            ? [...(a.skills ?? []), skillKey]
            : (a.skills ?? []).filter((s) => s !== skillKey);
          const response = await authedFetch(
            `${clientBase()}/v1/agents/${encodeURIComponent(a.key)}/versions`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ...a, skills: newSkills, version: a.version + 1 }),
            },
          );
          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw toApiError(
              body?.error ?? { message: `分配失败 ${a.key}: ${response.status}` },
              response.status,
            );
          }
        }),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });
}

// ---- Prompts（Prompt 库一等实体，独立版本化，Agent 引用快照落地） ----
export function usePrompts(): UseQueryResult<PromptDefinition[], ApiError> {
  return useQuery({ queryKey: ['prompts'], queryFn: () => unwrap(client.GET('/v1/prompts')) });
}

export function usePrompt(key: string): UseQueryResult<PromptDefinition, ApiError> {
  return useQuery({
    queryKey: ['prompts', key],
    queryFn: () => unwrap(client.GET('/v1/prompts/{key}', { params: { path: { key } } })),
    enabled: key !== '',
  });
}

export function usePromptVersions(key: string): UseQueryResult<PromptVersion[], ApiError> {
  return useQuery({
    queryKey: ['prompts', key, 'versions'],
    queryFn: () => unwrap(client.GET('/v1/prompts/{key}/versions', { params: { path: { key } } })),
    enabled: key !== '',
  });
}

export function useCreatePrompt(): UseMutationResult<PromptDefinition, ApiError, PromptDefinition> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition) => unwrap(client.POST('/v1/prompts', { body: definition })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['prompts'] }),
  });
}

export function useCreatePromptVersion(
  key: string,
): UseMutationResult<PromptVersion, ApiError, PromptDefinition> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition) =>
      unwrap(
        client.POST('/v1/prompts/{key}/versions', {
          params: { path: { key } },
          body: definition,
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
      queryClient.invalidateQueries({ queryKey: ['prompts', key] });
    },
  });
}

export interface AssignPromptArgs {
  promptKey: string;
  /** agentKey → 期望引用状态（true=引用；缺省=取消引用转内嵌） */
  targets: Record<string, boolean>;
  /** 当前 Agent 全量定义（算新版本用）；仅对状态变化的 Agent 建版本 */
  agents: AgentDefinition[];
}

/**
 * 分配 Prompt 到多个 Agent：对状态变化的 Agent 各 POST 一个新版本（prompt_key ± 引用）。
 * 服务端落库前解析 prompt 当前版内容写入快照（instructions = prompt content）。
 */
export function useAssignPromptToAgents(): UseMutationResult<void, ApiError, AssignPromptArgs> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ promptKey, targets, agents }) => {
      await Promise.all(
        agents.map(async (a) => {
          const wasAssigned = a.prompt_key === promptKey;
          const shouldBe = targets[a.key] === true;
          if (wasAssigned === shouldBe) return; // 无变化跳过
          const response = await authedFetch(
            `${clientBase()}/v1/agents/${encodeURIComponent(a.key)}/versions`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                ...a,
                prompt_key: shouldBe ? promptKey : null,
                // 分配即升级：显式清空钉住版本 → 服务端解析 prompt 当前版写入快照
                prompt_version: null,
                version: a.version + 1,
              }),
            },
          );
          if (!response.ok) {
            const body = await response.json().catch(() => null);
            throw toApiError(
              body?.error ?? { message: `分配失败 ${a.key}: ${response.status}` },
              response.status,
            );
          }
        }),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
    },
  });
}

// ---- Traces ----
export function useTrace(runId: string): UseQueryResult<TraceOut, ApiError> {
  return useQuery({
    queryKey: ['traces', runId],
    queryFn: () =>
      unwrap(client.GET('/v1/traces/{run_id}', { params: { path: { run_id: runId } } })),
    enabled: runId !== '',
  });
}

// ---- 审批 ----
export function useApprovals(runId?: string): UseQueryResult<ApprovalOut[], ApiError> {
  return useQuery({
    queryKey: ['approvals', runId ?? ''],
    queryFn: () =>
      unwrap(
        client.GET('/v1/approvals', {
          params: { query: runId === undefined ? {} : { run_id: runId } },
        }),
      ),
  });
}

export function useDecideApproval(): UseMutationResult<
  DecideOut,
  ApiError,
  { approvalId: string } & DecideRequest
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ approvalId, ...body }) =>
      unwrap(
        client.POST('/v1/approvals/{approval_id}/decide', {
          params: { path: { approval_id: approvalId } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['approvals'] }),
  });
}

// ---- 评测报告（R5 评测体系打通：列表 + 详情读真实 eval_results） ----
export function useEvals(): UseQueryResult<EvalReportSummary[], ApiError> {
  return useQuery({ queryKey: ['evals'], queryFn: () => unwrap(client.GET('/v1/evals')) });
}

export function useEvalDetail(evalId: number | null): UseQueryResult<EvalReportDetail, ApiError> {
  return useQuery({
    queryKey: ['evals', 'detail', evalId],
    queryFn: () =>
      unwrap(
        client.GET('/v1/evals/{result_id}', {
          params: { path: { result_id: evalId as number } },
        }),
      ),
    enabled: evalId !== null,
  });
}

// ---- Knowledge（工作台知识库管理） ----
export function useKnowledgeSources(): UseQueryResult<KnowledgeSourceOut[], ApiError> {
  return useQuery({
    queryKey: ['knowledge'],
    queryFn: () => unwrap(client.GET('/v1/knowledge/sources')),
  });
}

export interface UploadKnowledgeSourceArgs {
  name: string;
  description: string;
  chunk_size: number;
  overlap: number;
  files: File[];
}

/** 上传文档创建知识源（multipart/form-data；不经 openapi-fetch，直接 fetch 复用 clientBase） */
export function useUploadKnowledgeSource(): UseMutationResult<
  KnowledgeSourceOut,
  ApiError,
  UploadKnowledgeSourceArgs
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, description, chunk_size, overlap, files }) => {
      const form = new FormData();
      form.append('name', name);
      form.append('description', description);
      form.append('chunk_size', String(chunk_size));
      form.append('overlap', String(overlap));
      for (const file of files) form.append('files', file);
      const response = await authedFetch(`${clientBase()}/v1/knowledge/sources`, {
        method: 'POST',
        body: form,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw toApiError(
          body?.error ?? { message: `上传失败: ${response.status}` },
          response.status,
        );
      }
      return (await response.json()) as KnowledgeSourceOut;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['knowledge'] }),
  });
}

/** 删除知识源（仅托管源；块+落盘文件+注册全清） */
export function useDeleteKnowledgeSource(): UseMutationResult<void, ApiError, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name) => {
      const response = await authedFetch(
        `${clientBase()}/v1/knowledge/sources/${encodeURIComponent(name)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw toApiError(
          body?.error ?? { message: `删除失败: ${response.status}` },
          response.status,
        );
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['knowledge'] }),
  });
}

export function useKnowledgeChunks(name: string): UseQueryResult<KnowledgeChunkOut[], ApiError> {
  return useQuery({
    queryKey: ['knowledge', name, 'chunks'],
    queryFn: () =>
      unwrap(
        client.GET('/v1/knowledge/sources/{name}/chunks', {
          params: { path: { name } },
        }),
      ),
    enabled: name !== '',
  });
}

/** 重新切块：按新参数从落盘文件重灌（覆盖现有块） */
export function useRechunkKnowledgeSource(
  name: string,
): UseMutationResult<KnowledgeSourceOut, ApiError, KnowledgeRechunkRequest> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      unwrap(
        client.POST('/v1/knowledge/sources/{name}/rechunk', {
          params: { path: { name } },
          body,
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge'] });
      queryClient.invalidateQueries({ queryKey: ['knowledge', name, 'chunks'] });
    },
  });
}

/** 检索测试：query + 可选 source 过滤 → 命中块（含 score） */
export function useKnowledgeQuery(): UseMutationResult<
  KnowledgeHitOut[],
  ApiError,
  KnowledgeQueryRequest
> {
  return useMutation({
    mutationFn: (body) => unwrap(client.POST('/v1/knowledge/query', { body })),
  });
}

// ---- Graphs（只读结构视图）----
export type GraphSummaryOut = components['schemas']['GraphSummaryOut'];
export type GraphDetailOut = components['schemas']['GraphDetailOut'];

export function useGraphs(): UseQueryResult<GraphSummaryOut[], ApiError> {
  return useQuery({ queryKey: ['graphs'], queryFn: () => unwrap(client.GET('/v1/graphs')) });
}

export function useGraphDetail(name: string | null): UseQueryResult<GraphDetailOut, ApiError> {
  return useQuery({
    queryKey: ['graphs', name],
    queryFn: () => unwrap(client.GET('/v1/graphs/{name}', { params: { path: { name: name! } } })),
    enabled: name !== null,
  });
}
