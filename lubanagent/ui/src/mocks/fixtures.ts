/**
 * Mock fixtures：数据形态对着 src/api/schema.d.ts 写（契约 D4-1 边界：不编造字段）。
 * 2 Agent / 3 模型 / 四槽位映射 / 4 工具 / 2 Skill / 3 Run + Trace 事件流 / 审批 / 评测报告。
 */

import type { components } from '@/api/schema';

type AgentDefinition = components['schemas']['AgentDefinition'];
type AgentVersion = components['schemas']['AgentVersion'];
type ApprovalOut = components['schemas']['ApprovalOut'];
type BrainMappingOut = components['schemas']['BrainMappingOut'];
type ModelDefinitionOut = components['schemas']['ModelDefinitionOut'];
type Run = components['schemas']['Run'];
type SkillOut = components['schemas']['SkillOut'];
type ToolDescriptor = components['schemas']['ToolDescriptor'];
type TraceEvent = components['schemas']['TraceEvent'];
type TraceOut = components['schemas']['TraceOut'];

const NOW = '2026-07-23T10:00:00Z';

// ---- Agents（2 个） ----
export const agents: AgentDefinition[] = [
  {
    key: 'cs-agent',
    name: '智能客服',
    description: '内置验证场景：订单查询 + 退款审批',
    role: '客服专员',
    instructions: '你是 {company} 的客服专员，先安抚再解决，退款必须走审批。',
    greeting: '你好，我是 LubanAgent。请问有什么可以帮你？',
    avatar: 'avatar.svg',
    prompt_variables: { company: 'Luban 商店' },
    status: 'published',
    version: 3,
    tools: ['query-order', 'refund'],
    skills: ['cs-playbook'],
    tool_signatures: { 'query-order': 'a1b2c3', refund: 'd4e5f6' },
    brain: { profile: 'smart', allow_run_override: true, fallback_profiles: ['fast'] },
    boundary: {
      max_steps: 16,
      timeout_seconds: 300,
      allow_human_handoff: true,
      budget: { max_tokens: 20000, max_cost: 1.0, max_seconds: null },
    },
    io: { input_type: 'text', output_schema: null, streaming: true },
    memory: { session: true, long_term: false, summary: true, window_size: 20 },
    knowledge: {
      cite: false,
      sources: [],
      top_k: 5,
      score_threshold: 0,
      no_hit_fallback: null,
      max_age_days: null,
    },
    mcp_servers: [],
  },
  {
    key: 'code-helper',
    name: '代码助手',
    description: '读取代码片段并给出修改建议',
    role: '资深工程师',
    instructions: '回答先给结论，代码块用 markdown。',
    greeting: '',
    prompt_variables: {},
    status: 'draft',
    version: 1,
    tools: ['read-snippet'],
    skills: [],
    tool_signatures: { 'read-snippet': 'f7a8b9' },
    brain: { profile: 'fast', allow_run_override: false, fallback_profiles: [] },
    boundary: { max_steps: 8, timeout_seconds: 120, allow_human_handoff: false, budget: null },
    io: { input_type: 'text', output_schema: null, streaming: true },
    memory: { session: false, long_term: false, summary: false, window_size: 10 },
    knowledge: {
      cite: false,
      sources: [],
      top_k: 5,
      score_threshold: 0,
      no_hit_fallback: null,
      max_age_days: null,
    },
    mcp_servers: [],
  },
];

export const agentVersions: Record<string, AgentVersion[]> = {
  'cs-agent': [1, 2, 3].map((version) => ({
    agent_key: 'cs-agent',
    version,
    status: version === 3 ? 'published' : 'deprecated',
    published_at: `2026-07-2${version}T0${version}:00:00Z`,
    definition: { ...agents[0], version },
  })),
  'code-helper': [
    {
      agent_key: 'code-helper',
      version: 1,
      status: 'draft',
      published_at: null,
      definition: agents[1],
    },
  ],
};

// ---- 模型（3 个，脱敏视图：无 base_url） ----
export const models: ModelDefinitionOut[] = [
  {
    key: 'deepseek-chat',
    provider: 'deepseek',
    model_name: 'deepseek-chat',
    factory: 'openai_compatible',
    capability: {
      chat: true,
      streaming: true,
      tool_calling: true,
      vision: false,
      structured_output: true,
      reasoning: false,
      context_window: 64000,
      max_output: 8192,
    },
    params: { temperature: 0.3 },
    api_key_env: 'DEEPSEEK_API_KEY',
    enabled: true,
  },
  {
    key: 'qwen-plus',
    provider: 'qwen',
    model_name: 'qwen-plus',
    factory: 'openai_compatible',
    capability: {
      chat: true,
      streaming: true,
      tool_calling: true,
      vision: false,
      structured_output: false,
      reasoning: false,
      context_window: 128000,
      max_output: 4096,
    },
    params: { temperature: 0.7 },
    api_key_env: 'DASHSCOPE_API_KEY',
    enabled: true,
  },
  {
    key: 'glm-4v',
    provider: 'zhipu',
    model_name: 'glm-4v',
    factory: 'openai_compatible',
    capability: {
      chat: true,
      streaming: true,
      tool_calling: false,
      vision: true,
      structured_output: false,
      reasoning: false,
      context_window: 8192,
      max_output: 2048,
    },
    params: {},
    api_key_env: 'ZHIPU_API_KEY',
    enabled: false,
  },
];

// ---- 大脑映射（四槽位） ----
export const brainMappings: BrainMappingOut[] = [
  { env: 'dev', profile: 'fast', model_key: 'qwen-plus' },
  { env: 'dev', profile: 'smart', model_key: 'deepseek-chat' },
  { env: 'dev', profile: 'cheap', model_key: 'qwen-plus' },
  { env: 'dev', profile: 'vision', model_key: 'glm-4v' },
];

// ---- 工具（4 个：低/中/高风险 + 幂等示例） ----
export const tools: ToolDescriptor[] = [
  {
    key: 'query-order',
    name: '查询订单',
    description: '按订单号查询订单状态与物流',
    args_schema: {
      type: 'object',
      properties: { order_id: { type: 'string', description: '订单号' } },
      required: ['order_id'],
    },
    risk: 'low',
    contract: { timeout_seconds: 10, max_retries: 1, idempotent: true, approval_required: false },
    signature_hash: 'a1b2c3',
  },
  {
    key: 'refund',
    name: '退款',
    description: '对订单发起退款，高风险需审批',
    args_schema: {
      type: 'object',
      properties: {
        order_id: { type: 'string' },
        amount: { type: 'number', description: '退款金额（元）' },
        reason: { type: 'string' },
      },
      required: ['order_id', 'amount'],
    },
    risk: 'high',
    contract: {
      timeout_seconds: 30,
      max_retries: 0,
      idempotent: false,
      approval_required: true,
    },
    signature_hash: 'd4e5f6',
  },
  {
    key: 'read-snippet',
    name: '读取代码片段',
    description: '按路径与行号区间读取代码',
    args_schema: {
      type: 'object',
      properties: {
        path: { type: 'string' },
        start_line: { type: 'integer' },
        end_line: { type: 'integer' },
      },
      required: ['path'],
    },
    risk: 'low',
    contract: { timeout_seconds: 5, max_retries: 2, idempotent: true, approval_required: false },
    signature_hash: 'f7a8b9',
  },
  {
    key: 'send-notification',
    name: '发送通知',
    description: '向用户推送一条 IM 通知',
    args_schema: {
      type: 'object',
      properties: { user_id: { type: 'string' }, text: { type: 'string' } },
      required: ['user_id', 'text'],
    },
    risk: 'medium',
    contract: { timeout_seconds: 15, max_retries: 0, idempotent: false, approval_required: false },
    signature_hash: 'c0d1e2',
  },
];

// ---- Prompts（2 个，含版本历史） ----
type PromptDefinition = components['schemas']['PromptDefinition'];
type PromptVersion = components['schemas']['PromptVersion'];

export const prompts: PromptDefinition[] = [
  {
    key: 'cs-faq-prompt',
    name: 'FAQ 客服 Prompt',
    description: '退换货政策问答的基准指令',
    content: '你是 {company} 的客服 FAQ 专家，回答退换货政策与产品问题，不承诺知识库未覆盖的内容。',
    variables: { company: 'Luban 商店' },
    version: 2,
  },
  {
    key: 'refund-prompt',
    name: '退款安抚 Prompt',
    description: '先安抚再走退款流程',
    content: '你是 {company} 的退款专员，先安抚情绪，退款必须走审批。',
    variables: {},
    version: 1,
  },
];

export const promptVersions: Record<string, PromptVersion[]> = {
  'cs-faq-prompt': [
    {
      prompt_key: 'cs-faq-prompt',
      version: 2,
      content:
        '你是 {company} 的客服 FAQ 专家，回答退换货政策与产品问题，不承诺知识库未覆盖的内容。',
      variables: { company: 'Luban 商店' },
      created_at: NOW,
    },
    {
      prompt_key: 'cs-faq-prompt',
      version: 1,
      content: '你是 {company} 的客服 FAQ 专家。',
      variables: { company: 'Luban 商店' },
      created_at: NOW,
    },
  ],
  'refund-prompt': [
    {
      prompt_key: 'refund-prompt',
      version: 1,
      content: '你是 {company} 的退款专员，先安抚情绪，退款必须走审批。',
      variables: {},
      created_at: NOW,
    },
  ],
};

// ---- Skills（2 个） ----
export const skills: SkillOut[] = [
  {
    key: 'cs-playbook',
    name: '客服话术手册',
    version: '1.2.0',
    enabled: true,
    manifest: {
      name: 'cs-playbook',
      version: '1.2.0',
      description: '退换货标准流程与安抚话术',
      tools: ['query-order', 'refund'],
      entry: 'playbook.md',
    },
  },
  {
    key: 'sql-writer',
    name: 'SQL 生成',
    version: '0.3.1',
    enabled: false,
    manifest: {
      name: 'sql-writer',
      version: '0.3.1',
      description: '自然语言转只读 SQL',
      tools: [],
      entry: 'writer.md',
    },
  },
];

// ---- Runs（3 条：完成 / 失败 / 取消） ----
export const runs: Run[] = [
  {
    id: 'run-0001',
    agent_key: 'cs-agent',
    agent_version: 3,
    session_id: 'sess-1001',
    user_id: 'user-1001',
    parent_run_id: null,
    status: 'completed',
    input: { user_input: '我的订单 12345 到哪了？', variables: {} },
    output: '订单 12345 已到达杭州转运中心，预计明天送达。',
    brain_profile: 'smart',
    model_key: 'deepseek-chat',
    end_reason: 'final_answer',
    token_usage: { input_tokens: 812, output_tokens: 156, model_calls: 2 },
    cost: 0.012,
    error: null,
    created_at: '2026-07-23T09:58:12Z',
    started_at: '2026-07-23T09:58:12Z',
    ended_at: '2026-07-23T09:58:15Z',
  },
  {
    id: 'run-0002',
    agent_key: 'cs-agent',
    agent_version: 3,
    session_id: 'sess-1002',
    user_id: 'user-1002',
    parent_run_id: null,
    status: 'failed',
    input: { user_input: '帮我退款', variables: {} },
    output: null,
    brain_profile: 'smart',
    model_key: 'deepseek-chat',
    end_reason: 'error',
    token_usage: { input_tokens: 640, output_tokens: 0, model_calls: 1 },
    cost: 0.004,
    error: { code: 'TOOL_EXECUTION_FAILED', message: '工具 refund 执行失败: TimeoutError' },
    created_at: '2026-07-23T09:59:01Z',
    started_at: '2026-07-23T09:59:01Z',
    ended_at: '2026-07-23T09:59:32Z',
  },
  {
    id: 'run-0003',
    agent_key: 'code-helper',
    agent_version: 1,
    session_id: null,
    user_id: null,
    parent_run_id: null,
    status: 'cancelled',
    input: { user_input: '解释一下 quicksort', variables: {} },
    output: null,
    brain_profile: 'fast',
    model_key: 'qwen-plus',
    end_reason: 'cancelled',
    token_usage: { input_tokens: 128, output_tokens: 42, model_calls: 1 },
    cost: 0.001,
    error: null,
    created_at: NOW,
    started_at: NOW,
    ended_at: '2026-07-23T10:00:04Z',
  },
];

// ---- Trace 事件流（对应 run-0001，含步骤分组与工具调用） ----
const traceEvents: TraceEvent[] = [
  {
    id: 'evt-0001',
    run_id: 'run-0001',
    type: 'run_started',
    step_index: null,
    payload: {},
    created_at: '2026-07-23T09:58:12Z',
  },
  {
    id: 'evt-0002',
    run_id: 'run-0001',
    type: 'step_started',
    step_index: 0,
    payload: { kind: 'model' },
    created_at: '2026-07-23T09:58:12.100Z',
  },
  {
    id: 'evt-0003',
    run_id: 'run-0001',
    type: 'model_call_started',
    step_index: 0,
    payload: {
      model_key: 'deepseek-chat',
      input: [
        { role: 'system', content: '你是客服助手。' },
        { role: 'user', content: '订单 12345 到哪了？' },
      ],
    },
    created_at: '2026-07-23T09:58:12.120Z',
  },
  {
    id: 'evt-0004',
    run_id: 'run-0001',
    type: 'model_token_delta',
    step_index: 0,
    payload: { text: '好的，我先查一下订单 ' },
    created_at: '2026-07-23T09:58:12.800Z',
  },
  {
    id: 'evt-0005',
    run_id: 'run-0001',
    type: 'model_call_completed',
    step_index: 0,
    payload: {
      model_key: 'deepseek-chat',
      text: '好的，我先查一下订单',
      tool_calls: [],
      input_tokens: 512,
      output_tokens: 64,
      usage_known: true,
    },
    created_at: '2026-07-23T09:58:13.100Z',
  },
  {
    id: 'evt-0006',
    run_id: 'run-0001',
    type: 'tool_call_started',
    step_index: 1,
    payload: { tool: 'query-order', args: { order_id: '12345' } },
    created_at: '2026-07-23T09:58:13.200Z',
  },
  {
    id: 'evt-0007',
    run_id: 'run-0001',
    type: 'tool_call_completed',
    step_index: 1,
    payload: {
      tool: 'query-order',
      result: { order_id: '12345', status: '在途', node: '杭州转运中心' },
      duration_ms: 180,
    },
    created_at: '2026-07-23T09:58:13.400Z',
  },
  {
    id: 'evt-0008',
    run_id: 'run-0001',
    type: 'model_token_delta',
    step_index: 2,
    payload: { text: '订单 12345 已到达杭州转运中心，预计明天送达。' },
    created_at: '2026-07-23T09:58:14.600Z',
  },
  {
    id: 'evt-0009',
    run_id: 'run-0001',
    type: 'model_call_completed',
    step_index: 2,
    payload: {
      model_key: 'deepseek-chat',
      text: '订单 12345 已到达杭州转运中心，预计明天送达。',
      tool_calls: [],
      input_tokens: null,
      output_tokens: null,
      usage_known: false,
    },
    created_at: '2026-07-23T09:58:14.900Z',
  },
  {
    id: 'evt-0010',
    run_id: 'run-0001',
    type: 'run_completed',
    step_index: null,
    payload: {
      end_reason: 'final_answer',
      output: '订单 12345 已到达杭州转运中心，预计明天送达。',
    },
    created_at: '2026-07-23T09:58:15Z',
  },
];

export const traces: Record<string, TraceOut> = {
  'run-0001': {
    trace: {
      id: 'trace-0001',
      run_id: 'run-0001',
      status: 'completed',
      started_at: '2026-07-23T09:58:12Z',
      ended_at: '2026-07-23T09:58:15Z',
    },
    events: traceEvents,
  },
  'run-0002': {
    trace: {
      id: 'trace-0002',
      run_id: 'run-0002',
      status: 'failed',
      started_at: '2026-07-23T09:59:01Z',
      ended_at: '2026-07-23T09:59:32Z',
    },
    events: [
      {
        id: 'evt-0101',
        run_id: 'run-0002',
        type: 'run_started',
        step_index: null,
        payload: {},
        created_at: '2026-07-23T09:59:01Z',
      },
      {
        id: 'evt-0102',
        run_id: 'run-0002',
        type: 'tool_call_failed',
        step_index: 1,
        payload: {
          tool: 'refund',
          error: { code: 'TOOL_EXECUTION_FAILED', message: '工具 refund 执行失败: TimeoutError' },
        },
        created_at: '2026-07-23T09:59:32Z',
      },
      {
        id: 'evt-0103',
        run_id: 'run-0002',
        type: 'run_failed',
        step_index: null,
        payload: {
          end_reason: 'error',
          error: { code: 'TOOL_EXECUTION_FAILED', message: '工具 refund 执行失败: TimeoutError' },
        },
        created_at: '2026-07-23T09:59:32Z',
      },
    ],
  },
};

// ---- 审批（1 条待决） ----
export const approvals: ApprovalOut[] = [
  {
    id: 'appr-0001',
    run_id: 'run-0002',
    tool_key: 'refund',
    args: { order_id: '12345', amount: 99.5, reason: '商品破损' },
    created_at: '2026-07-23T09:59:10Z',
    status: 'pending',
  },
];

// ---- 评测报告（R5：真实 API 形态——列表摘要 + 详情逐 case 四类断言 verdicts） ----
type EvalReportSummary = components['schemas']['EvalReportSummary'];
type EvalReportDetail = components['schemas']['EvalReportDetail'];

export const evalReports: EvalReportSummary[] = [
  {
    id: 1,
    dataset_key: 'cs-regression-v3',
    agent_key: 'cs-agent',
    agent_version: 2,
    model_key: 'fake-chat',
    pass_rate: 0.5,
    passed_cases: 2,
    total_cases: 4,
    wrong_answer_rate: 0,
    created_at: '2026-07-22T20:00:00Z',
  },
  {
    id: 2,
    dataset_key: 'cs-regression-v4',
    agent_key: 'cs-agent',
    agent_version: 3,
    model_key: 'fake-chat',
    pass_rate: 0.75,
    passed_cases: 3,
    total_cases: 4,
    wrong_answer_rate: 0.5,
    created_at: '2026-07-23T08:30:00Z',
  },
];

export const evalReportDetails: Record<number, EvalReportDetail> = {
  2: {
    ...evalReports[1],
    results: [
      {
        case: 'refund-approval-flow',
        passed: true,
        verdicts: [
          { kind: 'output', passed: true, detail: '回复包含单号与安抚话术' },
          { kind: 'tool_call', passed: true, detail: 'refund 触发审批并等待决议' },
        ],
        run_id: 'run-0001',
        output: '已为您提交退款申请（订单 12345），审批通过后将原路退回。',
      },
      {
        case: 'order-cancel-unsupported',
        passed: false,
        verdicts: [
          { kind: 'output', passed: false, detail: '应引导人工而非承诺取消' },
          { kind: 'tool_call', passed: false, detail: '误调用 cancel-order（未注册）' },
        ],
        run_id: 'run-0002',
        output: '好的，已为您取消订单。',
      },
      {
        case: 'greeting-vars',
        passed: true,
        verdicts: [
          { kind: 'judge', passed: true, detail: 'judge 评分 5/4: 问候语自然，变量渲染正确' },
        ],
        run_id: 'run-0003',
        output: '你好，我是 {company} 的客服专员，很高兴为您服务。',
      },
      {
        case: 'policy-out-of-scope',
        passed: false,
        verdicts: [
          {
            kind: 'refusal',
            passed: false,
            detail: '应拒答未拒答（错答）：事件流无 knowledge_no_hit，模型在低置信检索下作答',
          },
          { kind: 'judge', passed: false, detail: 'judge 评分 2/4: 编造了不存在的政策', error: null },
        ],
        run_id: 'run-0004',
        output: '我们支持 30 天无理由退货（编造）。',
      },
    ],
  },
};

/** 新增 Run 的 id 序号（mock 运行调试台用） */
let runSeq = 100;
export function nextRunId(): string {
  runSeq += 1;
  return `run-mock-${runSeq}`;
}

// ---- Knowledge（工作台知识库：1 托管 + 1 内置） ----
type KnowledgeSourceOut = components['schemas']['KnowledgeSourceOut'];
type KnowledgeChunkOut = components['schemas']['KnowledgeChunkOut'];
type KnowledgeHitOut = components['schemas']['KnowledgeHitOut'];

export const knowledgeSources: KnowledgeSourceOut[] = [
  {
    name: 'team-wiki',
    description: '团队 wiki 文档',
    chunk_size: 500,
    overlap: 50,
    files: [
      { filename: 'onboarding.md', size: 4200 },
      { filename: 'faq.docx', size: 12800 },
    ],
    chunk_count: 24,
    managed: true,
    created_at: NOW,
    updated_at: NOW,
  },
  {
    name: 'product-faq',
    description: '',
    chunk_size: null,
    overlap: null,
    files: [],
    chunk_count: 12,
    managed: false,
    created_at: null,
    updated_at: null,
  },
];

export const knowledgeChunks: KnowledgeChunkOut[] = Array.from({ length: 24 }, (_, i) => ({
  chunk_index: i,
  content: `知识切块 ${i}：退货政策支持七天无理由退货，退货运费由商家承担。`,
  metadata: { filename: i < 12 ? 'onboarding.md' : 'faq.docx', ingested_at: NOW },
  created_at: NOW,
}));

export const knowledgeHits: KnowledgeHitOut[] = [
  {
    source: 'team-wiki',
    chunk_index: 3,
    content: '退货政策支持七天无理由退货，退货运费由商家承担。',
    score: 0.412,
    metadata: { filename: 'onboarding.md' },
  },
  {
    source: 'team-wiki',
    chunk_index: 11,
    content: '保修说明：整机保修一年，核心部件保修三年。',
    score: 0.265,
    metadata: { filename: 'faq.docx' },
  },
];

// ---- Graphs（与后端 refund_flow 真实结构对齐）----
export const graphSummaries = [
  {
    name: 'refund_flow',
    description: '退款流水线：校验→计算→审批→退款→通知',
    structure_available: true,
  },
];

export const refundFlowDetail = {
  name: 'refund_flow',
  description: '退款流水线：校验→计算→审批→退款→通知',
  entry: 'check_eligibility',
  nodes: [
    {
      name: 'check_eligibility',
      kind: 'step',
      agent_key: null,
      input_from: null,
      output_to: null,
      prompt: null,
      summary: '资格校验：订单存在且可退。',
    },
    {
      name: 'compute_amount',
      kind: 'step',
      agent_key: null,
      input_from: null,
      output_to: null,
      prompt: null,
      summary: '金额计算：取订单金额作为退款额（示例简化），标记是否需审批。',
    },
    {
      name: 'approve',
      kind: 'human',
      agent_key: null,
      input_from: null,
      output_to: null,
      prompt: '退款金额 >500 元，需要人工审批',
      summary: null,
    },
    {
      name: 'execute_refund',
      kind: 'step',
      agent_key: null,
      input_from: null,
      output_to: null,
      prompt: null,
      summary: '执行退款：经 Tool refund_apply（已含审批闸门，>500 由 Tool 治理拦截）。',
    },
    {
      name: 'notify',
      kind: 'step',
      agent_key: null,
      input_from: null,
      output_to: null,
      prompt: null,
      summary: '通知：构造用户可见的退款结果摘要。',
    },
  ],
  edges: [
    { from_node: 'check_eligibility', to_node: 'compute_amount' },
    { from_node: 'approve', to_node: 'execute_refund' },
    { from_node: 'execute_refund', to_node: 'notify' },
  ],
  cond_edges: [
    { from_node: 'compute_amount', then_node: 'approve', otherwise_node: 'execute_refund' },
  ],
};
