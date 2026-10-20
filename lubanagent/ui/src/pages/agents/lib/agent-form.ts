/**
 * Agent 表单辅助：定义归一化（可选段补默认值）、空定义模板、常量。
 * 数据形态对齐 schema.d.ts 的 AgentDefinition，不编造字段。
 */

import type { AgentDefinition, BrainProfile } from '@/api/hooks';

export const BRAIN_PROFILES = ['fast', 'smart', 'cheap', 'vision'] as const;

export const AGENT_STATUSES = ['draft', 'published', 'deprecated'] as const;

export const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已废弃',
};

/** 新建 Agent 对话框的最小完整定义（与 fixtures 同形态） */
export function emptyAgentDefinition(
  key: string,
  name: string,
  description: string,
  instructions: string,
  profile: BrainProfile,
): AgentDefinition {
  return normalizeDefinition({
    key,
    name,
    description,
    instructions,
    greeting: '',
    role: '',
    status: 'draft',
    version: 1,
    brain: { profile, allow_run_override: false },
  });
}

/** 补齐可选段默认值，让表单可以安全地深层读写 */
export function normalizeDefinition(def: AgentDefinition): AgentDefinition {
  return {
    ...def,
    description: def.description ?? '',
    greeting: def.greeting ?? '',
    instructions: def.instructions ?? '',
    role: def.role ?? '',
    prompt_variables: def.prompt_variables ?? {},
    tools: def.tools ?? [],
    skills: def.skills ?? [],
    tool_signatures: def.tool_signatures ?? {},
    mcp_servers: def.mcp_servers ?? [],
    brain: {
      profile: 'smart',
      allow_run_override: false,
      fallback_profiles: [],
      ...def.brain,
    },
    boundary: {
      max_steps: 16,
      timeout_seconds: 300,
      allow_human_handoff: false,
      budget: null,
      ...def.boundary,
    },
    io: { input_type: 'text', output_schema: null, streaming: true, ...def.io },
    memory: { session: true, long_term: false, summary: false, window_size: 20, ...def.memory },
    knowledge: {
      cite: true,
      sources: [],
      top_k: 5,
      score_threshold: 0,
      no_hit_fallback: null,
      max_age_days: null,
      ...def.knowledge,
    },
  };
}

/** number 输入解析：空/非法回退 fallback */
export function toNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return value.trim() === '' || Number.isNaN(parsed) ? fallback : parsed;
}

/** 可空 number 输入解析：空 → null */
export function toNullableNumber(value: string): number | null {
  if (value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}
