/**
 * Agent 定义分区表单（Tabs：身份/指令/大脑/能力/记忆与知识/边界/IO）。
 * 全受控：draft 由父层（编辑器页）持有，onChange 产出新 draft。
 * 校验只做 UX 层（JSON 格式提示），业务规则以后端为准。
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';

import type { AgentDefinition, BrainProfile } from '@/api/hooks';
import { usePrompts } from '@/api/hooks';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';

import {
  AGENT_STATUSES,
  BRAIN_PROFILES,
  STATUS_LABELS,
  toNullableNumber,
  toNumber,
} from '../lib/agent-form';
import { AvatarCard } from './avatar-card';
import { KeyValueEditor } from './key-value-editor';
import { TagInput } from './tag-input';

interface AgentFormProps {
  draft: AgentDefinition;
  onChange: (draft: AgentDefinition) => void;
  /** 只读形态（历史版本查看）：全部控件禁用 */
  readOnly?: boolean;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

/**
 * 指令来源双轨：内嵌编辑（默认，instructions 直接可改）或引用 Prompt 库
 * （prompt_key 非空——保存时服务端解析 prompt 当前版内容写入版本快照，此处只读预览）。
 */
function PromptSourceField({
  draft,
  readOnly,
  patch,
}: {
  draft: AgentDefinition;
  readOnly?: boolean;
  patch: (partial: Partial<AgentDefinition>) => void;
}) {
  const promptsQuery = usePrompts();
  const prompts = promptsQuery.data ?? [];
  const referenced = prompts.find((p) => p.key === draft.prompt_key) ?? null;

  if (draft.prompt_key) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-xs text-muted-foreground">系统指令（instructions）</Label>
          <span className="rounded-full border border-blue-500/50 px-2 py-0.5 font-mono text-xs text-blue-500">
            来自 prompt {draft.prompt_key} v{draft.prompt_version ?? referenced?.version ?? '?'}
          </span>
        </div>
        <pre className="max-h-64 overflow-y-auto rounded-md border bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap">
          {draft.instructions || '（空）'}
        </pre>
        <p className="text-xs text-muted-foreground">
          引用态为只读快照：保存版本时由服务端解析 Prompt 当前版内容写入；Prompt 新版不自动生效，
          需在 Prompt 库重新分配或此处保存新版本触发解析。去
          <a className="underline" href="#/prompts">
            Prompt 库
          </a>
          管理。
        </p>
        {!readOnly && (
          <button
            type="button"
            className="text-xs text-muted-foreground underline"
            onClick={() => patch({ prompt_key: null, prompt_version: null })}
          >
            转为内嵌编辑（解除引用，保留当前内容）
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Field label="系统指令（instructions，支持 {变量} 占位）">
        <Textarea
          value={draft.instructions ?? ''}
          rows={10}
          className="font-mono text-sm"
          disabled={readOnly}
          onChange={(event) => patch({ instructions: event.target.value })}
        />
      </Field>
      {prompts.length > 0 && !readOnly && (
        <Field label="引用 Prompt 库（保存时内容落地为本版本快照）">
          <Select
            value="none"
            onValueChange={(value) => {
              if (value === 'none') return;
              patch({ prompt_key: value, prompt_version: null });
            }}
          >
            <SelectTrigger aria-label="引用 Prompt" className="text-xs">
              <SelectValue placeholder="内嵌编辑（不引用）" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">内嵌编辑（不引用）</SelectItem>
              {prompts.map((p) => (
                <SelectItem key={p.key} value={p.key}>
                  {p.name}（{p.key} v{p.version}）
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      )}
    </div>
  );
}

function SwitchRow({
  label,
  hint,
  checked,
  onCheckedChange,
  disabled,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border px-3 py-2">
      <div>
        <p className="text-sm">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
    </div>
  );
}

export function AgentForm({ draft, onChange, readOnly = false }: AgentFormProps) {
  const brain = draft.brain ?? { profile: 'smart' as BrainProfile, allow_run_override: false };
  const boundary = draft.boundary ?? {
    max_steps: 16,
    timeout_seconds: 300,
    allow_human_handoff: false,
  };
  const io = draft.io ?? { input_type: 'text' as const, streaming: true };
  const memory = draft.memory ?? {
    session: true,
    long_term: false,
    summary: false,
    window_size: 20,
  };
  const knowledge = draft.knowledge ?? {
    cite: true,
    top_k: 5,
    score_threshold: 0,
    no_hit_fallback: null,
    max_age_days: null,
    sources: [],
  };
  const budget = boundary.budget ?? null;

  const patch = (partial: Partial<AgentDefinition>) => onChange({ ...draft, ...partial });

  const outputSchemaText = io.output_schema ? JSON.stringify(io.output_schema, null, 2) : '';

  return (
    <Tabs defaultValue="identity" className="w-full">
      <TabsList className="flex w-full flex-wrap justify-start">
        <TabsTrigger value="identity">身份</TabsTrigger>
        <TabsTrigger value="instructions">指令</TabsTrigger>
        <TabsTrigger value="brain">大脑</TabsTrigger>
        <TabsTrigger value="capabilities">能力</TabsTrigger>
        <TabsTrigger value="memory">记忆与知识</TabsTrigger>
        <TabsTrigger value="boundary">边界</TabsTrigger>
        <TabsTrigger value="io">IO</TabsTrigger>
      </TabsList>

      {/* ---- 身份 ---- */}
      <TabsContent value="identity" className="space-y-4 pt-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Key（创建后不可改）">
            <Input value={draft.key} readOnly disabled className="font-mono" />
          </Field>
          <Field label="名称">
            <Input
              value={draft.name}
              disabled={readOnly}
              onChange={(event) => patch({ name: event.target.value })}
            />
          </Field>
          <Field label="角色（role）">
            <Input
              value={draft.role ?? ''}
              placeholder="如：客服专员"
              disabled={readOnly}
              onChange={(event) => patch({ role: event.target.value })}
            />
          </Field>
          <Field label="状态">
            <Select
              value={draft.status}
              disabled={readOnly}
              onValueChange={(value) => patch({ status: value as AgentDefinition['status'] })}
            >
              <SelectTrigger aria-label="状态">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AGENT_STATUSES.map((status) => (
                  <SelectItem key={status} value={status}>
                    {STATUS_LABELS[status] ?? status}（{status}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
        <Field label="描述">
          <Textarea
            value={draft.description ?? ''}
            rows={2}
            disabled={readOnly}
            onChange={(event) => patch({ description: event.target.value })}
          />
        </Field>
        <Field label="开场白（greeting）">
          <Textarea
            value={draft.greeting ?? ''}
            rows={2}
            disabled={readOnly}
            onChange={(event) => patch({ greeting: event.target.value })}
          />
        </Field>
        {!readOnly && (
          <AvatarCard
            agent={draft}
            onSynced={(synced) => patch({ version: synced.version, avatar: synced.avatar })}
          />
        )}
      </TabsContent>

      {/* ---- 指令 ---- */}
      <TabsContent value="instructions" className="space-y-4 pt-4">
        <PromptSourceField draft={draft} readOnly={readOnly} patch={patch} />
        <Field label="Prompt 变量（prompt_variables）">
          <KeyValueEditor
            value={draft.prompt_variables ?? {}}
            disabled={readOnly}
            onChange={(variables) => patch({ prompt_variables: variables })}
          />
        </Field>
      </TabsContent>

      {/* ---- 大脑 ---- */}
      <TabsContent value="brain" className="space-y-4 pt-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="逻辑大脑槽位（profile）">
            <Select
              value={brain.profile}
              disabled={readOnly}
              onValueChange={(value) =>
                patch({ brain: { ...brain, profile: value as BrainProfile } })
              }
            >
              <SelectTrigger aria-label="逻辑大脑槽位">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BRAIN_PROFILES.map((profile) => (
                  <SelectItem key={profile} value={profile}>
                    {profile}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">降级槽位（fallback_profiles）</Label>
            <div className="flex flex-wrap gap-3 rounded-md border px-3 py-2">
              {BRAIN_PROFILES.filter((profile) => profile !== brain.profile).map((profile) => {
                const fallbacks = brain.fallback_profiles ?? [];
                const checked = fallbacks.includes(profile);
                return (
                  <label key={profile} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="checkbox"
                      className="accent-primary"
                      checked={checked}
                      disabled={readOnly}
                      onChange={(event) =>
                        patch({
                          brain: {
                            ...brain,
                            fallback_profiles: event.target.checked
                              ? [...fallbacks, profile]
                              : fallbacks.filter((item) => item !== profile),
                          },
                        })
                      }
                    />
                    {profile}
                  </label>
                );
              })}
            </div>
          </div>
        </div>
        <SwitchRow
          label="允许运行期覆盖（allow_run_override）"
          hint="打开后，单次 Run 可在请求里临时指定其他槽位"
          checked={brain.allow_run_override}
          disabled={readOnly}
          onCheckedChange={(checked) => patch({ brain: { ...brain, allow_run_override: checked } })}
        />
      </TabsContent>

      {/* ---- 能力 ---- */}
      <TabsContent value="capabilities" className="space-y-4 pt-4">
        <Field label="工具（tools）">
          <TagInput
            values={draft.tools ?? []}
            placeholder="工具 key，如 query-order"
            aria-label="添加工具"
            disabled={readOnly}
            onChange={(tools) => patch({ tools })}
          />
        </Field>
        <Field label="技能（skills）">
          <TagInput
            values={draft.skills ?? []}
            placeholder="Skill key"
            aria-label="添加技能"
            disabled={readOnly}
            onChange={(skills) => patch({ skills })}
          />
        </Field>
        <Field label="MCP 服务（mcp_servers）">
          <TagInput
            values={draft.mcp_servers ?? []}
            placeholder="MCP server 标识"
            aria-label="添加 MCP 服务"
            disabled={readOnly}
            onChange={(servers) => patch({ mcp_servers: servers })}
          />
        </Field>
      </TabsContent>

      {/* ---- 记忆与知识 ---- */}
      <TabsContent value="memory" className="space-y-4 pt-4">
        <div className="space-y-2">
          <SwitchRow
            label="会话记忆（memory.session）"
            checked={memory.session}
            disabled={readOnly}
            onCheckedChange={(checked) => patch({ memory: { ...memory, session: checked } })}
          />
          <SwitchRow
            label="长期记忆（memory.long_term）"
            checked={memory.long_term}
            disabled={readOnly}
            onCheckedChange={(checked) => patch({ memory: { ...memory, long_term: checked } })}
          />
          <SwitchRow
            label="会话摘要（memory.summary）"
            checked={memory.summary}
            disabled={readOnly}
            onCheckedChange={(checked) => patch({ memory: { ...memory, summary: checked } })}
          />
        </div>
        <Field label="记忆窗口（window_size，条）">
          <Input
            type="number"
            min={1}
            className="w-40"
            value={memory.window_size}
            disabled={readOnly}
            onChange={(event) =>
              patch({
                memory: {
                  ...memory,
                  window_size: toNumber(event.target.value, memory.window_size),
                },
              })
            }
          />
        </Field>
        <div className="space-y-2 rounded-md border p-3">
          <SwitchRow
            label="知识引用（knowledge.cite）"
            hint="回答中标注知识来源"
            checked={knowledge.cite}
            disabled={readOnly}
            onCheckedChange={(checked) => patch({ knowledge: { ...knowledge, cite: checked } })}
          />
          <Field label="知识源（knowledge.sources）">
            <TagInput
              values={knowledge.sources ?? []}
              placeholder="知识源 key"
              aria-label="添加知识源"
              disabled={readOnly}
              onChange={(sources) => patch({ knowledge: { ...knowledge, sources } })}
            />
          </Field>
          <Field label="检索条数（knowledge.top_k）">
            <Input
              type="number"
              min={1}
              className="w-40"
              value={knowledge.top_k}
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  knowledge: { ...knowledge, top_k: toNumber(event.target.value, knowledge.top_k) },
                })
              }
            />
          </Field>
          <Field label="相关度阈值（knowledge.score_threshold）">
            <Input
              type="number"
              min={0}
              max={1}
              step="0.01"
              className="w-40"
              value={knowledge.score_threshold}
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  knowledge: {
                    ...knowledge,
                    score_threshold: toNumber(event.target.value, knowledge.score_threshold),
                  },
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              cosine 低于该值的检索片段直接丢弃；0 为不过滤
            </p>
          </Field>
          <Field label="拒答兜底话术（knowledge.no_hit_fallback）">
            <Textarea
              rows={2}
              placeholder="留空 = 检索全空时仍走模型作答"
              value={knowledge.no_hit_fallback ?? ''}
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  knowledge: {
                    ...knowledge,
                    no_hit_fallback: event.target.value.trim() === '' ? null : event.target.value,
                  },
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              配置后检索全 miss 时代码强制短路拒答（不调模型），有效防错答
            </p>
          </Field>
          <Field label="知识时效（knowledge.max_age_days，天）">
            <Input
              type="number"
              min={0}
              className="w-40"
              value={knowledge.max_age_days ?? ''}
              placeholder="不限"
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  knowledge: {
                    ...knowledge,
                    max_age_days: toNullableNumber(event.target.value),
                  },
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              摄入时间超过该天数的片段丢弃；留空 = 不限
            </p>
          </Field>
        </div>
      </TabsContent>

      {/* ---- 边界 ---- */}
      <TabsContent value="boundary" className="space-y-4 pt-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="最大步数（max_steps）">
            <Input
              type="number"
              min={1}
              value={boundary.max_steps}
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  boundary: {
                    ...boundary,
                    max_steps: toNumber(event.target.value, boundary.max_steps),
                  },
                })
              }
            />
          </Field>
          <Field label="超时（timeout_seconds，秒）">
            <Input
              type="number"
              min={1}
              value={boundary.timeout_seconds}
              disabled={readOnly}
              onChange={(event) =>
                patch({
                  boundary: {
                    ...boundary,
                    timeout_seconds: toNumber(event.target.value, boundary.timeout_seconds),
                  },
                })
              }
            />
          </Field>
        </div>
        <SwitchRow
          label="允许转人工（allow_human_handoff）"
          checked={boundary.allow_human_handoff}
          disabled={readOnly}
          onCheckedChange={(checked) =>
            patch({ boundary: { ...boundary, allow_human_handoff: checked } })
          }
        />
        <div className="space-y-3 rounded-md border p-3">
          <p className="text-xs font-medium text-muted-foreground">预算（budget，留空 = 不限制）</p>
          <div className="grid gap-4 md:grid-cols-3">
            <Field label="max_tokens">
              <Input
                type="number"
                min={0}
                value={budget?.max_tokens ?? ''}
                placeholder="不限"
                disabled={readOnly}
                onChange={(event) =>
                  patch({
                    boundary: {
                      ...boundary,
                      budget: { ...budget, max_tokens: toNullableNumber(event.target.value) },
                    },
                  })
                }
              />
            </Field>
            <Field label="max_cost（元）">
              <Input
                type="number"
                min={0}
                step="0.01"
                value={budget?.max_cost ?? ''}
                placeholder="不限"
                disabled={readOnly}
                onChange={(event) =>
                  patch({
                    boundary: {
                      ...boundary,
                      budget: { ...budget, max_cost: toNullableNumber(event.target.value) },
                    },
                  })
                }
              />
            </Field>
            <Field label="max_seconds">
              <Input
                type="number"
                min={0}
                value={budget?.max_seconds ?? ''}
                placeholder="不限"
                disabled={readOnly}
                onChange={(event) =>
                  patch({
                    boundary: {
                      ...boundary,
                      budget: { ...budget, max_seconds: toNullableNumber(event.target.value) },
                    },
                  })
                }
              />
            </Field>
          </div>
        </div>
      </TabsContent>

      {/* ---- IO ---- */}
      <TabsContent value="io" className="space-y-4 pt-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="输入类型（input_type）">
            <Select
              value={io.input_type}
              disabled={readOnly}
              onValueChange={(value) =>
                patch({ io: { ...io, input_type: value as typeof io.input_type } })
              }
            >
              <SelectTrigger aria-label="输入类型">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="text">text</SelectItem>
                <SelectItem value="json">json</SelectItem>
                <SelectItem value="multimodal">multimodal</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <div className="flex items-end pb-1">
            <SwitchRow
              label="流式输出（streaming）"
              checked={io.streaming}
              disabled={readOnly}
              onCheckedChange={(checked) => patch({ io: { ...io, streaming: checked } })}
            />
          </div>
        </div>
        <OutputSchemaField
          value={outputSchemaText}
          disabled={readOnly}
          onValid={(schema) => patch({ io: { ...io, output_schema: schema } })}
        />
      </TabsContent>
    </Tabs>
  );
}

/** output_schema JSON 编辑：本地文本态，解析成功才提交（UX 层格式校验） */
function OutputSchemaField({
  value,
  disabled,
  onValid,
}: {
  value: string;
  disabled: boolean;
  onValid: (schema: Record<string, unknown> | null) => void;
}) {
  const [text, setText] = useState(value);
  const [error, setError] = useState<string | null>(null);
  // 自己提交的值不回填（避免边打字边被 pretty-print 打断）；仅外部变化时同步
  const selfCommitted = useRef<string | null>(null);

  useEffect(() => {
    if (selfCommitted.current === value) return;
    selfCommitted.current = null;
    setText(value);
    setError(null);
  }, [value]);

  return (
    <Field label="输出 Schema（output_schema，JSON，留空 = 自由文本）">
      <Textarea
        value={text}
        rows={6}
        className="font-mono text-xs"
        placeholder='{"type":"object", ...}'
        disabled={disabled}
        aria-label="输出 Schema"
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          if (next.trim() === '') {
            setError(null);
            selfCommitted.current = '';
            onValid(null);
            return;
          }
          try {
            const schema = JSON.parse(next) as Record<string, unknown>;
            selfCommitted.current = JSON.stringify(schema, null, 2);
            onValid(schema);
            setError(null);
          } catch {
            setError('JSON 格式错误，修正后才会生效');
          }
        }}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </Field>
  );
}
