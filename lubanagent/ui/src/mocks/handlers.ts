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
  runs,
  skills,
  tools,
  traces,
} from './fixtures';

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
    if (!detail) return errorOut(404, 'EVAL_RESULT_NOT_FOUND', `评测报告不存在: ${params.resultId}`);
    return HttpResponse.json(detail);
  }),

  // ---- Graphs（只读结构视图） ----
  http.get('*/v1/graphs', () => HttpResponse.json(graphSummaries)),
  http.get('*/v1/graphs/:name', ({ params }) => {
    if (params.name === 'refund_flow') return HttpResponse.json(refundFlowDetail);
    return errorOut(404, 'GRAPH_NOT_FOUND', `图未注册: ${params.name}`);
  }),
];
