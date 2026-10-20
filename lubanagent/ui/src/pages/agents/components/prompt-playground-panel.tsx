/**
 * Agent 调试台「Prompt 试跑」Tab：围绕单个 Agent 的 A/B 试跑面板。
 * 加载该 Agent 的 instructions/variables 到编辑态（不入库）→ 同一输入两次 SSE
 * 试跑（原定义 vs 修改后）→ 输出并排对比。修改后面板：variables 取编辑值
 * （请求体原生字段，即时生效）；instructions 与原定义不同时经 instructions_override
 * 临时替换（仅本次 Run 生效，不改版本化定义）——即"改定义即时试"。
 */

import { Play, Plus, RotateCcw, X } from 'lucide-react';
import { useState } from 'react';

import { API_BASE } from '@/api/client';
import { usePrompts, type AgentDefinition } from '@/api/hooks';
import { RunStreamView } from '@/components/run-stream-view';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useRunStream } from '@/lib/sse';

interface VariableRow {
  key: string;
  value: string;
}

function toVariablesRecord(rows: VariableRow[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter((row) => row.key.trim() !== '').map((row) => [row.key.trim(), row.value]),
  );
}

export function PromptPlaygroundPanel({ agent }: { agent: AgentDefinition }) {
  // 编辑器状态：按 agent.key 守卫播种（render 期同步，避免 useAgent refetch 覆盖编辑）
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [instructions, setInstructions] = useState('');
  const [variables, setVariables] = useState<VariableRow[]>([]);
  if (agent.key !== loadedKey) {
    setLoadedKey(agent.key);
    setInstructions(agent.instructions);
    setVariables(
      Object.entries(agent.prompt_variables ?? {}).map(([key, value]) => ({
        key,
        value: String(value),
      })),
    );
  }

  const [input, setInput] = useState('');
  const original = useRunStream();
  const modified = useRunStream();

  const running = original.status === 'streaming' || modified.status === 'streaming';
  const canRun = input.trim() !== '' && !running;

  const runBoth = () => {
    if (!canRun) return;
    const url = `${API_BASE}/v1/agents/${encodeURIComponent(agent.key)}/runs`;
    const body = input.trim();
    // 每次对比一条 session_id（original/modified 共享）：Trace 列表里成对可查；
    // 每次点击重新生成，避免开了 memory.session 的 Agent 跨轮累积会话记忆污染对比
    // （若需严格对比公平可改 `${sid}-a` / `${sid}-b` 后缀隔离）
    const sid = crypto.randomUUID();
    void original.start(url, {
      input: body,
      session_id: sid,
      variables: agent.prompt_variables ?? {},
    });
    // instructions 有改动时经 instructions_override 临时替换（仅本次 Run 生效）
    const instructionsChanged = instructions.trim() !== (agent.instructions ?? '').trim();
    void modified.start(url, {
      input: body,
      session_id: sid,
      variables: toVariablesRecord(variables),
      ...(instructionsChanged ? { instructions_override: instructions } : {}),
    });
  };

  const updateVariable = (index: number, patch: Partial<VariableRow>) => {
    setVariables((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <p className="text-xs text-muted-foreground">
        对比基线：库内定义 v{agent.version}。左 = 当前版本原样；右 = 你的修改（variables
        即时生效、instructions 临时替换，均不入库，满意后去「定义」发新版本）。
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">原定义 instructions</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">
              {agent.instructions || '（空）'}
            </pre>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm">修改后 instructions（不入库）</CardTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setInstructions(agent.instructions)}
              disabled={instructions === agent.instructions}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" />
              重置
            </Button>
          </CardHeader>
          <CardContent>
            <Textarea
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              rows={5}
              aria-label="修改后 instructions"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              修改后内容与原定义不同时，试跑经 instructions_override 临时生效，不改版本化定义。
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm">从 Prompt 库载入（填充右侧编辑态，不入库）</CardTitle>
        </CardHeader>
        <CardContent>
          <LoadFromPromptLibrary
            onLoad={(content, variables) => {
              setInstructions(content);
              setVariables(Object.entries(variables).map(([key, value]) => ({ key, value })));
            }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="text-sm">variables（右侧面板试跑生效）</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setVariables((rows) => [...rows, { key: '', value: '' }])}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            添加变量
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {variables.length === 0 && (
            <p className="text-xs text-muted-foreground">
              该 Agent 未定义 prompt_variables，可手动添加。
            </p>
          )}
          {variables.map((row, index) => (
            <div key={index} className="flex items-center gap-2">
              <Input
                value={row.key}
                onChange={(event) => updateVariable(index, { key: event.target.value })}
                placeholder="变量名"
                className="w-48 font-mono"
                aria-label={`变量名 ${index + 1}`}
              />
              <Input
                value={row.value}
                onChange={(event) => updateVariable(index, { value: event.target.value })}
                placeholder="变量值"
                className="flex-1"
                aria-label={`变量值 ${index + 1}`}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setVariables((rows) => rows.filter((_, i) => i !== index))}
                aria-label={`删除变量 ${index + 1}`}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Label htmlFor="playground-input" className="mb-1 block text-xs">
            试跑输入（两侧共用）
          </Label>
          <Textarea
            id="playground-input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            rows={2}
            placeholder="输入一条用户消息…"
          />
        </div>
        <Button onClick={runBoth} disabled={!canRun}>
          <Play className="mr-1 h-4 w-4" />
          {running ? '试跑中…' : '并排试跑'}
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card data-testid="panel-original">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">原定义 · 试跑结果</CardTitle>
          </CardHeader>
          <CardContent>
            {original.events.length === 0 && original.status === 'idle' ? (
              <p className="text-xs text-muted-foreground">尚未试跑。</p>
            ) : (
              <RunStreamView
                events={original.events}
                status={original.status}
                error={original.error}
              />
            )}
          </CardContent>
        </Card>
        <Card data-testid="panel-modified">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">修改后 · 试跑结果</CardTitle>
          </CardHeader>
          <CardContent>
            {modified.events.length === 0 && modified.status === 'idle' ? (
              <p className="text-xs text-muted-foreground">尚未试跑。</p>
            ) : (
              <RunStreamView
                events={modified.events}
                status={modified.status}
                error={modified.error}
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** 从 Prompt 库选一条，把 content/variables 填充到编辑态（走 instructions_override，仅本次 Run 生效） */
function LoadFromPromptLibrary({
  onLoad,
}: {
  onLoad: (content: string, variables: Record<string, string>) => void;
}) {
  const promptsQuery = usePrompts();
  const prompts = promptsQuery.data ?? [];
  const [key, setKey] = useState('');

  if (prompts.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Prompt 库为空——去 <a className="underline" href="#/prompts">Prompt 库</a> 新建后可在此直接载入调试。
      </p>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Select value={key} onValueChange={setKey}>
        <SelectTrigger className="w-72" aria-label="从 Prompt 库载入">
          <SelectValue placeholder="选择一条 Prompt" />
        </SelectTrigger>
        <SelectContent>
          {prompts.map((p) => (
            <SelectItem key={p.key} value={p.key}>
              {p.name}（{p.key} v{p.version}）
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        size="sm"
        disabled={key === ''}
        onClick={() => {
          const prompt = prompts.find((p) => p.key === key);
          if (prompt) onLoad(prompt.content, prompt.variables ?? {});
        }}
      >
        载入到编辑态
      </Button>
      <span className="text-xs text-muted-foreground">载入后经 instructions_override 试跑，不改库内定义</span>
    </div>
  );
}
