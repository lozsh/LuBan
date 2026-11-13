/**
 * MSW handlers：覆盖全部已接路由（契约 D4-1 决策 4）。
 * 错误形状对齐后端 ErrorOut；POST /v1/agents/:key/runs 用 ReadableStream 流式吐 SSE。
 * 路径统一带通配前缀（星号 + /v1/...）：MSW 2.15 的 path-only 谓词不再跨 origin
 * 匹配，通配前缀同时覆盖浏览器（dev server origin）与 vitest node（http://localhost）。
 */

import { delay, http, HttpResponse } from 'msw';

import type { ErrorOut } from '@/api/client';
import type { components } from '@/api/schema';

import {
  agentVersions,
  agents,
  approvals,
  brainMappings,
  brainProfiles,
  adminUser,
  devUser,
  diagnosticChildRun,
  diagnosticLogs,
  evalReportDetails,
  evalReports,
  graphSummaries,
  knowledgeChunks,
  knowledgeHits,
  knowledgeSources,
  models,
  nextRunId,
  promptVersions,
  prompts,
  refundFlowDetail,
  roles,
  runs,
  skills,
  securityUsers,
  permissions,
  tools,
  traces,
  operatorUser,
  viewerUser,
} from './fixtures';

import type { RoleOut, UserUpsert, UserPatch, RoleUpsert } from '@/api/access';
import type { SessionUser } from '@/lib/auth';

type AgentDefinition = components['schemas']['AgentDefinition'];
type AgentVersion = components['schemas']['AgentVersion'];
type BrainMappingSetRequest = components['schemas']['BrainMappingSetRequest'];
type ModelDefinition = components['schemas']['ModelDefinition'];
type Run = components['schemas']['Run'];
type RunCreateRequest = components['schemas']['RunCreateRequest'];
type TraceEvent = components['schemas']['TraceEvent'];

function errorOut(status: number, code: string, message: string): HttpResponse<ErrorOut> {
  return HttpResponse.json({ error: { code, message, details: {}, retryable: false } }, { status });
}

function formatSse(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 模拟一次完整 Run 的 SSE 帧序列（对齐后端 SSE_EVENT_MAP 的 data = {run_id, type, ...payload}） */
function mockRunFrames(runId: string, input: string): Array<[string, Record<string, unknown>]> {
  const reply = `收到「${input}」。订单 12345 已到达杭州转运中心，预计明天送达。`;
  return [
    ['run.started', { run_id: runId, type: 'run_started' }],
    ['token.delta', { run_id: runId, type: 'model_token_delta', text: '好的，' }],
    ['token.delta', { run_id: runId, type: 'model_token_delta', text: '我先查一下订单。' }],
    [
      'tool.started',
      {
        run_id: runId,
        type: 'tool_call_started',
        tool: 'query-order',
        args: { order_id: '12345' },
      },
    ],
    [
      'tool.finished',
      {
        run_id: runId,
        type: 'tool_call_completed',
        tool: 'query-order',
        result: { order_id: '12345', status: '在途', node: '杭州转运中心' },
        duration_ms: 132,
      },
    ],
    ['token.delta', { run_id: runId, type: 'model_token_delta', text: reply }],
    [
      'run.completed',
      { run_id: runId, type: 'run_completed', end_reason: 'final_answer', output: reply },
    ],
  ];
}

/** mock 产生的 Run 同步登记到 runs/traces，Trace 列表与回放立即可见 */
function registerMockRun(runId: string, agentKey: string, input: string): void {
  const agent = agents.find((item) => item.key === agentKey);
  const now = new Date().toISOString();
  const reply = `收到「${input}」。订单 12345 已到达杭州转运中心，预计明天送达。`;
  const run: Run = {
    id: runId,
    agent_key: agentKey,
    agent_version: agent?.version ?? 1,
    session_id: null,
    parent_run_id: null,
    status: 'completed',
    input: { user_input: input, variables: {} },
    output: reply,
    brain_profile: agent?.brain?.profile ?? 'smart',
    model_key: 'deepseek-chat',
    end_reason: 'final_answer',
    token_usage: { input_tokens: 512, output_tokens: 128, model_calls: 2 },
    cost: 0.006,
    error: null,
    created_at: now,
    started_at: now,
    ended_at: now,
  };
  runs.unshift(run);

  const events: TraceEvent[] = mockRunFrames(runId, input).map(([, data], index) => ({
    id: `evt-${runId}-${index}`,
    run_id: runId,
    type: data.type as TraceEvent['type'],
    step_index: null,
    payload: Object.fromEntries(
      Object.entries(data).filter(([key]) => key !== 'run_id' && key !== 'type'),
    ),
    created_at: now,
  }));
  traces[runId] = {
    trace: {
      id: `trace-${runId}`,
      run_id: runId,
      status: 'completed',
      started_at: now,
      ended_at: now,
    },
    events,
  };
}

function paginate<T>(
  items: T[],
  request: Request,
): { items: T[]; total: number; limit: number; offset: number } {
  const url = new URL(request.url);
  const limit = Math.min(200, Math.max(1, Number(url.searchParams.get('limit') ?? 20)));
  const offset = Math.max(0, Number(url.searchParams.get('offset') ?? 0));
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset };
}

function sessionFor(request: Request): SessionUser | null {
  const token = request.headers.get('Authorization')?.replace(/^Bearer\s+/i, '');
  if (token === 'session-admin') return adminUser;
  if (token === 'session-operator') return operatorUser;
  if (token === 'session-viewer') return viewerUser;
  // 本地开发（AUTH_ENABLED=false）默认授出全部权限；前端不自行推断这个状态。
  return devUser;
}

function slotReferences(slot: components['schemas']['BrainProfileOut']) {
  const keys = [slot.key, ...slot.aliases];
  const histories = { ...agentVersions };
  for (const agent of agents) {
    if (histories[agent.key]?.some((version) => version.version === agent.version)) continue;
    histories[agent.key] = [
      ...(histories[agent.key] ?? []),
      {
        agent_key: agent.key,
        version: agent.version,
        status: agent.status,
        published_at: null,
        definition: agent,
      },
    ];
  }
  return Object.entries(histories).flatMap(([agentKey, versions]) =>
    versions.flatMap((version) => {
      const definition = version.definition;
      const fields: string[] = [];
      if (keys.includes(definition.brain?.profile ?? 'smart')) fields.push('brain.profile');
      if (definition.brain?.fallback_profiles?.some((key) => keys.includes(key)))
        fields.push('brain.fallback_profiles');
      if (keys.includes('cheap') && definition.memory?.summary) fields.push('memory.summary');
      return fields.map((field) => ({
        agent_key: agentKey,
        agent_name: definition.name,
        version: version.version,
        current: agents.find((agent) => agent.key === agentKey)?.version === version.version,
        field,
      }));
    }),
  );
}

function slotOut(slot: components['schemas']['BrainProfileOut']) {
  const references = slotReferences(slot);
  return {
    ...slot,
    reference_count: references.length,
    agent_count: new Set(references.map((ref) => ref.agent_key)).size,
  };
}

function configWriteDenied(request: Request) {
  const permissions = sessionFor(request)?.permissions ?? [];
  return permissions.includes('*') || permissions.includes('config:write')
    ? null
    : errorOut(403, 'PERMISSION_DENIED', '缺少权限: config:write');
}

// mock 也保留已删除标识，避免演示中换槽位身份；单测局部 handler 不改共享目录。
const deletedBrainProfiles = new Map<string, components['schemas']['BrainProfileOut']>();

export const handlers = [
  http.get('*/health', () => HttpResponse.json({ status: 'ok' })),

  // ---- Agents ----
  http.get('*/v1/agents', () => HttpResponse.json(agents)),

  http.post('*/v1/agents', async ({ request }) => {
    const definition = (await request.json()) as AgentDefinition;
    if (agents.some((agent) => agent.key === definition.key)) {
      return errorOut(409, 'VALIDATION_ERROR', `Agent key 重复: ${definition.key}`);
    }
    agents.push(definition);
    return HttpResponse.json(definition, { status: 201 });
  }),

  http.get('*/v1/agents/:key', ({ params }) => {
    const agent = agents.find((item) => item.key === params.key);
    if (!agent) return errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${params.key}`);
    return HttpResponse.json(agent);
  }),

  // ---- Avatar（Agent 虚拟形象：svg mock；上传即版本） ----
  http.get('*/v1/agents/:key/avatar', ({ params }) => {
    const agent = agents.find((item) => item.key === params.key);
    if (!agent) return errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${params.key}`);
    if (!agent.avatar) {
      return errorOut(404, 'AVATAR_NOT_FOUND', `Agent 未设置形象: ${params.key}`);
    }
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="30" fill="#4f46e5"/></svg>`;
    return new HttpResponse(svg, {
      status: 200,
      headers: { 'Content-Type': 'image/svg+xml' },
    });
  }),

  http.put('*/v1/agents/:key/avatar', async ({ params, request }) => {
    const agent = agents.find((item) => item.key === params.key);
    if (!agent) return errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${params.key}`);
    const form = await request.formData();
    const file = form.get('file');
    if (!(file instanceof File)) {
      return errorOut(422, 'VALIDATION_ERROR', '缺少形象文件');
    }
    const suffix = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (!['svg', 'png', 'glb', 'gltf'].includes(suffix)) {
      return errorOut(422, 'VALIDATION_ERROR', `不支持的形象类型: ${file.name}`);
    }
    agent.avatar = `avatar.${suffix}`;
    agent.version += 1;
    return HttpResponse.json(agent);
  }),

  http.delete('*/v1/agents/:key/avatar', ({ params }) => {
    const agent = agents.find((item) => item.key === params.key);
    if (!agent) return errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${params.key}`);
    if (!agent.avatar) {
      return errorOut(404, 'AVATAR_NOT_FOUND', `Agent 未设置形象: ${params.key}`);
    }
    agent.avatar = null;
    agent.version += 1;
    return HttpResponse.json({ removed: true });
  }),

  http.post('*/v1/agents/:key/versions', async ({ params, request }) => {
    const key = params.key as string;
    if (!agents.some((agent) => agent.key === key)) {
      return errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${key}`);
    }
    const definition = (await request.json()) as AgentDefinition;
    const versions = (agentVersions[key] ??= []);
    if (versions.some((version) => version.version === definition.version)) {
      return errorOut(
        409,
        'VALIDATION_ERROR',
        `版本已存在且不可覆盖: ${key} v${definition.version}`,
      );
    }
    const snapshot: AgentVersion = {
      agent_key: key,
      version: definition.version,
      status: definition.status ?? 'draft',
      published_at: new Date().toISOString(),
      definition,
    };
    versions.push(snapshot);
    return HttpResponse.json(snapshot, { status: 201 });
  }),

  http.get('*/v1/agents/:key/versions/:version', ({ params }) => {
    const key = params.key as string;
    const version = Number(params.version);
    const snapshot = agentVersions[key]?.find((item) => item.version === version);
    if (!snapshot) {
      const exists = agents.some((agent) => agent.key === key);
      return exists
        ? errorOut(404, 'AGENT_VERSION_NOT_FOUND', `Agent 版本不存在: ${key} v${version}`)
        : errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${key}`);
    }
    return HttpResponse.json(snapshot);
  }),

  // ---- Runs（SSE 流式） ----
  http.post('*/v1/agents/:key/runs', async ({ params, request }) => {
    const key = params.key as string;
    if (!agents.some((agent) => agent.key === key)) {
      return errorOut(404, 'AGENT_NOT_FOUND', `Agent 不存在: ${key}`);
    }
    const body = (await request.json()) as RunCreateRequest;
    const runId = nextRunId();
    const frames = mockRunFrames(runId, body.input ?? '');
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        for (const [event, data] of frames) {
          controller.enqueue(encoder.encode(formatSse(event, data)));
          await delay(60);
        }
        controller.close();
      },
    });
    registerMockRun(runId, key, body.input ?? '');
    return new HttpResponse(stream, {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
      },
    });
  }),

  http.get('*/v1/runs', ({ request }) => {
    const url = new URL(request.url);
    const agentKey = url.searchParams.get('agent_key');
    const sessionId = url.searchParams.get('session_id');
    const limit = Number(url.searchParams.get('limit') ?? 50);
    let result = runs;
    if (agentKey) result = result.filter((run) => run.agent_key === agentKey);
    if (sessionId) result = result.filter((run) => run.session_id === sessionId);
    return HttpResponse.json(result.slice(0, limit));
  }),

  http.get('*/v1/runs/:runId', ({ params }) => {
    const run = runs.find((item) => item.id === params.runId);
    if (!run) return errorOut(404, 'RUN_NOT_FOUND', `Run 不存在: ${params.runId}`);
    return HttpResponse.json(run);
  }),

  http.post('*/v1/runs/:runId/cancel', ({ params }) => {
    const run = runs.find((item) => item.id === params.runId);
    if (!run) return errorOut(404, 'RUN_NOT_FOUND', `Run 不存在: ${params.runId}`);
    if (run.status === 'running' || run.status === 'pending') {
      run.status = 'cancelled';
      run.end_reason = 'cancelled';
      return HttpResponse.json({ cancelled: true });
    }
    return HttpResponse.json({ cancelled: false });
  }),

  // ---- 模型中心 ----
  http.get('*/v1/brain-profiles', () =>
    HttpResponse.json({ env: 'dev', items: brainProfiles.map(slotOut) }),
  ),

  http.post('*/v1/brain-profiles', async ({ request }) => {
    const denied = configWriteDenied(request);
    if (denied) return denied;
    const body = (await request.json()) as components['schemas']['BrainProfileCreate'];
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(body.key) || !body.name.trim())
      return errorOut(422, 'VALIDATION_ERROR', '名称或标识不合法');
    if (brainProfiles.some((slot) => slot.key === body.key || slot.aliases.includes(body.key)))
      return errorOut(409, 'BRAIN_PROFILE_CONFLICT', '槽位标识已存在');
    if (body.model_key && !models.some((model) => model.key === body.model_key))
      return errorOut(404, 'MODEL_NOT_FOUND', '模型不存在');
    const slot = {
      ...deletedBrainProfiles.get(body.key),
      key: body.key,
      name: body.name.trim(),
      description: body.description ?? '',
      aliases: deletedBrainProfiles.get(body.key)?.aliases ?? [],
      reference_count: 0,
      agent_count: 0,
    };
    brainProfiles.push(slot);
    deletedBrainProfiles.delete(body.key);
    if (body.model_key)
      brainMappings.push({ env: body.env ?? 'dev', profile: body.key, model_key: body.model_key });
    return HttpResponse.json(slotOut(slot), { status: 201 });
  }),

  http.patch('*/v1/brain-profiles/:key', async ({ params, request }) => {
    const denied = configWriteDenied(request);
    if (denied) return denied;
    const slot = brainProfiles.find(
      (item) => item.key === params.key || item.aliases.includes(String(params.key)),
    );
    if (!slot) return errorOut(404, 'BRAIN_PROFILE_NOT_FOUND', '槽位不存在');
    const body = (await request.json()) as components['schemas']['BrainProfilePatch'];
    if (
      (body.key && !/^[a-z][a-z0-9_-]{0,63}$/.test(body.key)) ||
      (body.name !== undefined && !body.name?.trim())
    )
      return errorOut(422, 'VALIDATION_ERROR', '名称或标识不合法');
    if (
      body.key &&
      brainProfiles.some(
        (other) => other !== slot && (other.key === body.key || other.aliases.includes(body.key!)),
      )
    )
      return errorOut(409, 'BRAIN_PROFILE_CONFLICT', '槽位标识已存在');
    if (body.model_key && !models.some((model) => model.key === body.model_key))
      return errorOut(404, 'MODEL_NOT_FOUND', '模型不存在');
    if (body.key && body.key !== slot.key) {
      const oldKey = slot.key;
      slot.aliases = [...new Set([...slot.aliases.filter((key) => key !== body.key), oldKey])];
      slot.key = body.key;
      for (const mapping of brainMappings)
        if (mapping.profile === oldKey) mapping.profile = slot.key;
    }
    if (body.name) slot.name = body.name.trim();
    if (body.description !== undefined && body.description !== null)
      slot.description = body.description.trim();
    if (body.model_key) {
      const env = body.env ?? 'dev';
      const existing = brainMappings.find(
        (mapping) => mapping.env === env && mapping.profile === slot.key,
      );
      if (existing) existing.model_key = body.model_key;
      else brainMappings.push({ env, profile: slot.key, model_key: body.model_key });
    }
    return HttpResponse.json(slotOut(slot));
  }),

  http.delete('*/v1/brain-profiles/:key', ({ params, request }) => {
    const denied = configWriteDenied(request);
    if (denied) return denied;
    const slot = brainProfiles.find(
      (item) => item.key === params.key || item.aliases.includes(String(params.key)),
    );
    if (!slot) return errorOut(404, 'BRAIN_PROFILE_NOT_FOUND', '槽位不存在');
    const references = slotReferences(slot);
    if (references.length)
      return HttpResponse.json(
        {
          error: {
            code: 'BRAIN_PROFILE_IN_USE',
            message: '槽位正被 Agent 使用，不能删除',
            details: { profile: slot.key, references },
          },
        },
        { status: 409 },
      );
    brainProfiles.splice(brainProfiles.indexOf(slot), 1);
    deletedBrainProfiles.set(slot.key, slot);
    for (let index = brainMappings.length - 1; index >= 0; index--)
      if (brainMappings[index].profile === slot.key) brainMappings.splice(index, 1);
    return HttpResponse.json({ removed: true });
  }),

  http.get('*/v1/brain-mappings', ({ request }) => {
    const env = new URL(request.url).searchParams.get('env') ?? 'dev';
    return HttpResponse.json(brainMappings.filter((mapping) => mapping.env === env));
  }),

  http.get('*/v1/brain-profiles/:key/references', ({ params }) => {
    const slot = brainProfiles.find(
      (item) => item.key === params.key || item.aliases.includes(String(params.key)),
    );
    if (!slot) return errorOut(404, 'BRAIN_PROFILE_NOT_FOUND', '槽位不存在');
    const references = slotReferences(slot);
    return HttpResponse.json({
      key: slot.key,
      agent_count: new Set(references.map((ref) => ref.agent_key)).size,
      references,
    });
  }),

  http.get('*/v1/models', () => HttpResponse.json(models)),

  http.put('*/v1/models', async ({ request }) => {
    const definition = (await request.json()) as ModelDefinition;
    const masked = {
      key: definition.key,
      provider: definition.provider,
      model_name: definition.model_name,
      factory: definition.factory,
      capability: definition.capability,
      params: definition.params ?? {},
      api_key_env: definition.api_key_env ?? null,
      enabled: definition.enabled ?? true,
    };
    const index = models.findIndex((model) => model.key === masked.key);
    if (index >= 0) models[index] = masked;
    else models.push(masked);
    return HttpResponse.json(masked);
  }),

  http.put('*/v1/brain-mappings', async ({ request }) => {
    const body = (await request.json()) as BrainMappingSetRequest;
    const env = body.env ?? 'dev';
    if (!models.some((model) => model.key === body.model_key)) {
      return errorOut(404, 'MODEL_NOT_FOUND', `模型不存在: ${body.model_key}`);
    }
    const existing = brainMappings.find(
      (mapping) => mapping.env === env && mapping.profile === body.profile,
    );
    if (existing) existing.model_key = body.model_key;
    else brainMappings.push({ env, profile: body.profile, model_key: body.model_key });
    return HttpResponse.json({ env, profile: body.profile, model_key: body.model_key });
  }),

  // ---- Tool 中心 ----
  http.get('*/v1/tools', () => HttpResponse.json(tools)),

  http.post('*/v1/tools/:key/test', async ({ params, request }) => {
    const tool = tools.find((item) => item.key === params.key);
    if (!tool) return errorOut(404, 'TOOL_NOT_FOUND', `工具未注册: ${params.key}`);
    const args = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const required = (tool.args_schema?.required as string[] | undefined) ?? [];
    const missing = required.filter((name) => !(name in args));
    if (missing.length > 0) {
      return HttpResponse.json({
        success: false,
        value: null,
        error: {
          code: 'VALIDATION_ERROR',
          message: `工具 ${tool.key} 参数校验失败`,
          details: { tool: tool.key, missing },
          retryable: false,
        },
        duration_ms: 0,
        attempts: 0,
      });
    }
    await delay(120);
    return HttpResponse.json({
      success: true,
      value: { echo: args, tool: tool.key, note: 'mock 执行结果' },
      error: null,
      duration_ms: 118,
      attempts: 1,
    });
  }),

  // ---- Prompts ----
  http.get('*/v1/prompts', () => HttpResponse.json(prompts)),

  http.get('*/v1/prompts/:key', ({ params }) => {
    const prompt = prompts.find((p) => p.key === params.key);
    if (!prompt) return errorOut(404, 'PROMPT_NOT_FOUND', `Prompt 不存在: ${params.key}`);
    return HttpResponse.json(prompt);
  }),

  http.get('*/v1/prompts/:key/versions', ({ params }) => {
    const versions = promptVersions[params.key as string];
    if (!versions) return errorOut(404, 'PROMPT_NOT_FOUND', `Prompt 不存在: ${params.key}`);
    return HttpResponse.json(versions);
  }),

  http.post('*/v1/prompts', async ({ request }) => {
    const body = (await request.json()) as { key?: string; name?: string };
    if (prompts.some((p) => p.key === body.key)) {
      return errorOut(409, 'VALIDATION_ERROR', `Prompt key 重复: ${body.key}`);
    }
    const created = {
      key: body.key ?? 'new-prompt',
      name: body.name ?? 'new-prompt',
      description: '',
      content: '',
      variables: {},
      version: 1,
    };
    return HttpResponse.json(created, { status: 201 });
  }),

  http.post('*/v1/prompts/:key/versions', async ({ params, request }) => {
    const body = (await request.json()) as { version?: number };
    const created = {
      prompt_key: params.key as string,
      version: body.version ?? 2,
      content: '',
      variables: {},
      created_at: new Date().toISOString(),
    };
    return HttpResponse.json(created, { status: 201 });
  }),

  // ---- Skills ----
  http.get('*/v1/skills', () => HttpResponse.json(skills)),

  http.post('*/v1/skills', async () => {
    const out = {
      key: 'uploaded-mock',
      name: 'uploaded-mock',
      version: '1.0.0',
      enabled: true,
      manifest: { name: 'uploaded-mock', version: '1.0.0', description: 'mock 上传' },
    };
    return HttpResponse.json(out, { status: 201 });
  }),

  http.delete('*/v1/skills/:key', () => new HttpResponse(null, { status: 204 })),

  // ---- Traces ----
  http.get('*/v1/traces/:runId', ({ params }) => {
    const trace = traces[params.runId as string];
    if (!trace) return errorOut(404, 'RUN_NOT_FOUND', `Run 不存在: ${params.runId}`);
    return HttpResponse.json(trace);
  }),

  // ---- 审批 ----
  http.get('*/v1/approvals', ({ request }) => {
    const url = new URL(request.url);
    const runId = url.searchParams.get('run_id');
    const result = runId ? approvals.filter((item) => item.run_id === runId) : approvals;
    return HttpResponse.json(result);
  }),

  http.post('*/v1/approvals/:approvalId/decide', async ({ params, request }) => {
    const approval = approvals.find((item) => item.id === params.approvalId);
    if (!approval) return errorOut(404, 'VALIDATION_ERROR', `审批不存在: ${params.approvalId}`);
    const body = (await request.json()) as { approved: boolean };
    approval.status = body.approved ? 'approved' : 'rejected';
    return HttpResponse.json({ status: approval.status });
  }),

  // ---- Knowledge（工作台知识库） ----
  http.get('*/v1/knowledge/sources', () => HttpResponse.json(knowledgeSources)),

  http.post('*/v1/knowledge/sources', async ({ request }) => {
    const form = await request.formData();
    const name = form.get('name');
    if (typeof name !== 'string' || !name) {
      return errorOut(422, 'VALIDATION_ERROR', '知识源名必填');
    }
    if (knowledgeSources.some((item) => item.name === name)) {
      return errorOut(409, 'VALIDATION_ERROR', `知识源已存在: ${name}`);
    }
    const chunkSize = Number(form.get('chunk_size') ?? 500) || 500;
    const overlap = Number(form.get('overlap') ?? 50) || 0;
    const files = form.getAll('files').filter((f): f is File => f instanceof File);
    const created = {
      name,
      description: String(form.get('description') ?? ''),
      chunk_size: chunkSize,
      overlap,
      files: files.map((f) => ({ filename: f.name, size: f.size })),
      chunk_count: files.length * 4,
      managed: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    return HttpResponse.json(created, { status: 201 });
  }),

  http.delete('*/v1/knowledge/sources/:name', ({ params }) => {
    const index = knowledgeSources.findIndex((item) => item.name === params.name);
    if (index === -1)
      return errorOut(404, 'KNOWLEDGE_SOURCE_NOT_FOUND', `知识源不存在: ${params.name}`);
    if (!knowledgeSources[index].managed) {
      return errorOut(422, 'VALIDATION_ERROR', '仅支持删除通过工作台创建的知识源');
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.post('*/v1/knowledge/sources/:name/rechunk', async ({ params, request }) => {
    const source = knowledgeSources.find((item) => item.name === params.name);
    if (!source) {
      return errorOut(404, 'KNOWLEDGE_SOURCE_NOT_FOUND', `知识源不存在: ${params.name}`);
    }
    const body = (await request.json()) as { chunk_size: number; overlap: number };
    return HttpResponse.json({ ...source, chunk_size: body.chunk_size, overlap: body.overlap });
  }),

  http.get('*/v1/knowledge/sources/:name/chunks', ({ request, params }) => {
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get('limit') ?? 200) || 200;
    if (!knowledgeSources.some((item) => item.name === params.name)) {
      return errorOut(404, 'KNOWLEDGE_SOURCE_NOT_FOUND', `知识源不存在: ${params.name}`);
    }
    return HttpResponse.json(knowledgeChunks.slice(0, limit));
  }),

  http.post('*/v1/knowledge/query', async ({ request }) => {
    const body = (await request.json()) as { query: string; source?: string | null };
    if (body.source && !knowledgeSources.some((item) => item.name === body.source)) {
      return errorOut(422, 'VALIDATION_ERROR', `知识源未注册: ${body.source}`);
    }
    return HttpResponse.json(knowledgeHits);
  }),

  // ---- 评测报告（列表 + 详情，读 eval_results 形态） ----
  http.get('*/v1/evals', () => HttpResponse.json(evalReports)),
  http.get('*/v1/evals/:resultId', ({ params }) => {
    const detail = evalReportDetails[Number(params.resultId)];
    if (!detail)
      return errorOut(404, 'EVAL_RESULT_NOT_FOUND', `评测报告不存在: ${params.resultId}`);
    return HttpResponse.json(detail);
  }),

  // ---- Graphs（只读结构视图） ----
  http.get('*/v1/graphs', () => HttpResponse.json(graphSummaries)),
  http.get('*/v1/graphs/:name', ({ params }) => {
    if (params.name === 'refund_flow') return HttpResponse.json(refundFlowDetail);
    return errorOut(404, 'GRAPH_NOT_FOUND', `图未注册: ${params.name}`);
  }),

  // ---- Auth / Security（账号登录 + RBAC） ----
  http.post('*/v1/auth/login', async ({ request }) => {
    const body = (await request.json()) as { username?: string; password?: string };
    const account =
      body.username === 'alice' && body.password === 'admin123'
        ? { user: adminUser, token: 'session-admin' }
        : body.username === 'bob' && body.password === 'operator123'
          ? { user: operatorUser, token: 'session-operator' }
          : null;
    if (!account) {
      return errorOut(401, 'AUTH_FAILED', '用户名或密码错误');
    }
    return HttpResponse.json({
      access_token: account.token,
      token_type: 'bearer',
      expires_at: '2026-07-23T11:00:00Z',
      user: account.user,
    });
  }),

  http.get('*/v1/auth/me', ({ request }) => {
    const user = sessionFor(request);
    if (!user) return errorOut(401, 'AUTH_REQUIRED', '未登录');
    return HttpResponse.json(user);
  }),

  http.post('*/v1/auth/logout', () => HttpResponse.json({ logged_out: true })),

  http.get('*/v1/security/users', () => HttpResponse.json(securityUsers)),

  http.post('*/v1/security/users', async ({ request }) => {
    const body = (await request.json()) as UserUpsert;
    if (!body.username || securityUsers.some((user) => user.username === body.username)) {
      return errorOut(409, 'VALIDATION_ERROR', `用户名必填或已存在: ${body.username ?? ''}`);
    }
    const now = new Date().toISOString();
    const user = {
      id: `u-${securityUsers.length + 1}`,
      username: body.username,
      display_name: body.display_name ?? body.username,
      enabled: body.enabled ?? true,
      role_keys: body.role_keys ?? [],
      created_at: now,
      updated_at: now,
    };
    securityUsers.push(user);
    return HttpResponse.json(user, { status: 201 });
  }),

  http.patch('*/v1/security/users/:id', async ({ params, request }) => {
    const user = securityUsers.find((item) => item.id === params.id);
    if (!user) return errorOut(404, 'USER_NOT_FOUND', `用户不存在: ${params.id}`);
    const patch = (await request.json()) as UserPatch;
    if (patch.display_name !== undefined) user.display_name = patch.display_name;
    if (patch.enabled !== undefined) user.enabled = patch.enabled;
    if (patch.role_keys !== undefined) user.role_keys = patch.role_keys;
    user.updated_at = new Date().toISOString();
    return HttpResponse.json(user);
  }),

  http.get('*/v1/security/roles', () => HttpResponse.json(roles)),

  http.get('*/v1/security/permissions', () => HttpResponse.json(permissions)),

  http.post('*/v1/security/roles', async ({ request }) => {
    const body = (await request.json()) as RoleUpsert;
    if (!body.key || roles.some((role) => role.key === body.key)) {
      return errorOut(409, 'VALIDATION_ERROR', `角色 key 必填或已存在: ${body.key ?? ''}`);
    }
    const role: RoleOut = {
      key: body.key,
      name: body.name || body.key,
      permissions: body.permissions,
    };
    roles.push(role);
    return HttpResponse.json(role, { status: 201 });
  }),

  http.patch('*/v1/security/roles/:key', async ({ params, request }) => {
    const role = roles.find((item) => item.key === params.key);
    if (!role) return errorOut(404, 'ROLE_NOT_FOUND', `角色不存在: ${params.key}`);
    if (role.protected) return errorOut(409, 'PROTECTED_ROLE', 'admin 角色受保护');
    const body = (await request.json()) as RoleUpsert;
    role.name = body.name || role.name;
    role.permissions = body.permissions;
    return HttpResponse.json(role);
  }),

  http.delete('*/v1/security/roles/:key', ({ params }) => {
    const index = roles.findIndex((role) => role.key === params.key);
    if (index === -1) return errorOut(404, 'ROLE_NOT_FOUND', `角色不存在: ${params.key}`);
    if (roles[index].protected) return errorOut(409, 'PROTECTED_ROLE', 'admin 角色受保护');
    roles.splice(index, 1);
    return HttpResponse.json({ deleted: true });
  }),

  // ---- 统一日志查询 ----
  http.get('*/v1/logs', ({ request }) => {
    const url = new URL(request.url);
    // 对齐真实后端约束：limit 上限 200（Query le=200），超限返回 422。
    if (Number(url.searchParams.get('limit') ?? 20) > 200) {
      return HttpResponse.json(
        {
          error: {
            code: 'VALIDATION_ERROR',
            message: '请求参数校验失败',
            details: {
              errors: [{ loc: ['query', 'limit'], type: 'less_than_equal', msg: '≤200' }],
            },
          },
        },
        { status: 422 },
      );
    }
    const equals = (
      value: string | null | undefined,
      expected: string | null | undefined,
    ): boolean => value === undefined || value === null || value === '' || expected === value;
    const keyword = url.searchParams.get('q')?.toLowerCase() ?? '';
    const filtered = diagnosticLogs.filter((log) => {
      const createdAt = new Date(log.created_at).getTime();
      const from = url.searchParams.get('from_time');
      const to = url.searchParams.get('to_time');
      return (
        equals(url.searchParams.get('session_id'), log.session_id) &&
        equals(url.searchParams.get('run_id'), log.run_id) &&
        equals(url.searchParams.get('root_run_id'), log.root_run_id) &&
        equals(url.searchParams.get('trace_id'), log.trace_id) &&
        equals(url.searchParams.get('request_id'), log.request_id) &&
        equals(url.searchParams.get('subject'), log.subject) &&
        equals(url.searchParams.get('level'), log.level) &&
        equals(url.searchParams.get('logger'), log.logger) &&
        (!from || createdAt >= new Date(from).getTime()) &&
        (!to || createdAt <= new Date(to).getTime()) &&
        (keyword === '' ||
          `${log.message} ${log.exception ?? ''} ${log.logger}`.toLowerCase().includes(keyword))
      );
    });
    return HttpResponse.json({
      ...paginate(filtered, request),
      dropped_count: 3,
      write_failures: 0,
    });
  }),

  http.get('*/v1/diagnostics/runs', ({ request }) => {
    const url = new URL(request.url);
    const equals = (
      value: string | null | undefined,
      expected: string | null | undefined,
    ): boolean => value === undefined || value === null || expected === value;
    const filtered = [...runs, diagnosticChildRun].filter((run) => {
      const status = url.searchParams.get('status');
      return (
        equals(url.searchParams.get('session_id'), run.session_id) &&
        equals(url.searchParams.get('run_id'), run.id) &&
        equals(url.searchParams.get('trace_id'), traces[run.id]?.trace.id ?? null) &&
        equals(url.searchParams.get('user_id'), run.user_id) &&
        (status === null || status === '' || run.status === status)
      );
    });
    return HttpResponse.json(paginate(filtered, request));
  }),

  http.get('*/v1/diagnostics/runs/:identifier', ({ params }) => {
    const identifier = params.identifier as string;
    const primary =
      [...runs, diagnosticChildRun].find((run) => run.id === identifier) ??
      (traces[identifier]
        ? ([...runs, diagnosticChildRun].find((run) => traces[run.id]?.trace.id === identifier) ??
          null)
        : null);
    if (!primary) return errorOut(404, 'RUN_NOT_FOUND', `排查对象不存在: ${identifier}`);
    const rootId = primary.parent_run_id ?? primary.id;
    const treeRuns = [...runs, diagnosticChildRun].filter(
      (run) => run.id === rootId || run.parent_run_id === rootId,
    );
    const treeTraces = treeRuns
      .map((run) => traces[run.id])
      .filter((trace): trace is NonNullable<typeof trace> => trace !== undefined);
    const logCount = diagnosticLogs.filter((log) => log.root_run_id === rootId).length;
    return HttpResponse.json({
      run: primary,
      runs: treeRuns,
      traces: treeTraces,
      log_count: logCount,
    });
  }),
];
