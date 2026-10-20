/**
 * 新建/编辑模型 Dialog：PUT /v1/models（upsert）。
 * 表单含 capability 开关组与 params 键值对；base_url 只进不出（列表脱敏不回显）。
 */

import { Plus, X } from 'lucide-react';
import { useState } from 'react';

import { useUpsertModel, type ModelDefinition, type ModelDefinitionOut } from '@/api/hooks';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';

const CAPABILITY_SWITCHES = [
  { key: 'chat', label: 'chat' },
  { key: 'streaming', label: 'streaming' },
  { key: 'tool_calling', label: 'tool_calling' },
  { key: 'vision', label: 'vision' },
  { key: 'structured_output', label: 'structured_output' },
  { key: 'reasoning', label: 'reasoning' },
] as const;

type CapabilityKey = (typeof CAPABILITY_SWITCHES)[number]['key'];

type CapabilityState = Record<CapabilityKey, boolean>;

interface ParamRow {
  id: number;
  name: string;
  value: string;
}

interface ModelFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 编辑时传入已有模型（脱敏视图，无 base_url）；新建传 null */
  initial: ModelDefinitionOut | null;
}

let paramRowSeq = 0;
function nextParamRowId(): number {
  paramRowSeq += 1;
  return paramRowSeq;
}

function paramsToRows(params: Record<string, unknown> | undefined): ParamRow[] {
  return Object.entries(params ?? {})
    .filter(([, value]) => value !== '[REDACTED]')
    .map(([name, value]) => ({
      id: nextParamRowId(),
      name,
      value: JSON.stringify(value),
    }));
}

export function ModelFormDialog({ open, onOpenChange, initial }: ModelFormDialogProps) {
  const upsert = useUpsertModel();
  const editing = initial !== null;

  const [key, setKey] = useState(initial?.key ?? '');
  const [provider, setProvider] = useState(initial?.provider ?? '');
  const [modelName, setModelName] = useState(initial?.model_name ?? '');
  const [factory, setFactory] = useState(initial?.factory ?? 'openai_compatible');
  const [apiKeyEnv, setApiKeyEnv] = useState(initial?.api_key_env ?? '');
  const [baseUrl, setBaseUrl] = useState('');
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [capability, setCapability] = useState<CapabilityState>({
    chat: initial?.capability?.chat ?? true,
    streaming: initial?.capability?.streaming ?? true,
    tool_calling: initial?.capability?.tool_calling ?? false,
    vision: initial?.capability?.vision ?? false,
    structured_output: initial?.capability?.structured_output ?? false,
    reasoning: initial?.capability?.reasoning ?? false,
  });
  const [contextWindow, setContextWindow] = useState(
    String(initial?.capability?.context_window ?? 8192),
  );
  const [maxOutput, setMaxOutput] = useState(String(initial?.capability?.max_output ?? 4096));
  const [paramRows, setParamRows] = useState<ParamRow[]>(() => paramsToRows(initial?.params));
  const [formError, setFormError] = useState<string | null>(null);

  function updateParamRow(id: number, patch: Partial<ParamRow>): void {
    setParamRows((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

  function handleSubmit(): void {
    setFormError(null);
    if (!key.trim() || !provider.trim() || !modelName.trim()) {
      setFormError('key / provider / model_name 为必填');
      return;
    }
    const params: Record<string, unknown> = {};
    for (const row of paramRows) {
      const name = row.name.trim();
      if (name === '') continue;
      try {
        params[name] = JSON.parse(row.value);
      } catch {
        setFormError(`参数 ${name} 的值不是合法 JSON`);
        return;
      }
    }
    const definition: ModelDefinition = {
      key: key.trim(),
      provider: provider.trim(),
      model_name: modelName.trim(),
      factory: factory.trim() || 'openai_compatible',
      api_key_env: apiKeyEnv.trim() || null,
      base_url: baseUrl.trim() || null,
      enabled,
      capability: {
        ...capability,
        context_window: Number.parseInt(contextWindow, 10) || 8192,
        max_output: Number.parseInt(maxOutput, 10) || 4096,
      },
      params,
    };
    upsert.mutate(definition, {
      onSuccess: () => onOpenChange(false),
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? `编辑模型 — ${initial.key}` : '新建模型'}</DialogTitle>
          <DialogDescription>
            保存即 upsert（PUT /v1/models）；连接信息只进不出，列表始终脱敏。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="model-key">key *</Label>
              <Input
                id="model-key"
                value={key}
                disabled={editing}
                placeholder="deepseek-chat"
                onChange={(event) => setKey(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="model-provider">provider *</Label>
              <Input
                id="model-provider"
                value={provider}
                placeholder="deepseek"
                onChange={(event) => setProvider(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="model-name">model_name *</Label>
              <Input
                id="model-name"
                value={modelName}
                placeholder="deepseek-chat"
                onChange={(event) => setModelName(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="model-factory">factory</Label>
              <Input
                id="model-factory"
                value={factory}
                placeholder="openai_compatible"
                onChange={(event) => setFactory(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="model-api-key-env">api_key_env</Label>
              <Input
                id="model-api-key-env"
                value={apiKeyEnv}
                placeholder="DEEPSEEK_API_KEY"
                onChange={(event) => setApiKeyEnv(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="model-base-url">base_url</Label>
              <Input
                id="model-base-url"
                value={baseUrl}
                placeholder="https://...（脱敏，不回显）"
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch id="model-enabled" checked={enabled} onCheckedChange={setEnabled} />
            <Label htmlFor="model-enabled">启用</Label>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">能力</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {CAPABILITY_SWITCHES.map((item) => (
                <div key={item.key} className="flex items-center gap-2">
                  <Switch
                    id={`cap-${item.key}`}
                    checked={capability[item.key]}
                    onCheckedChange={(checked) =>
                      setCapability((prev) => ({ ...prev, [item.key]: checked }))
                    }
                  />
                  <Label htmlFor={`cap-${item.key}`} className="font-mono text-xs">
                    {item.label}
                  </Label>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="cap-context-window">context_window</Label>
                <Input
                  id="cap-context-window"
                  type="number"
                  value={contextWindow}
                  onChange={(event) => setContextWindow(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="cap-max-output">max_output</Label>
                <Input
                  id="cap-max-output"
                  type="number"
                  value={maxOutput}
                  onChange={(event) => setMaxOutput(event.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">params（生成参数，值为 JSON）</p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() =>
                  setParamRows((rows) => [...rows, { id: nextParamRowId(), name: '', value: '' }])
                }
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                添加参数
              </Button>
            </div>
            {paramRows.length === 0 && (
              <p className="text-xs text-muted-foreground">暂无参数，如 temperature、top_p。</p>
            )}
            {paramRows.map((row) => (
              <div key={row.id} className="flex items-center gap-2">
                <Input
                  aria-label="参数名"
                  className="w-40 font-mono text-xs"
                  placeholder="temperature"
                  value={row.name}
                  onChange={(event) => updateParamRow(row.id, { name: event.target.value })}
                />
                <Input
                  aria-label="参数值"
                  className="flex-1 font-mono text-xs"
                  placeholder="0.3"
                  value={row.value}
                  onChange={(event) => updateParamRow(row.id, { value: event.target.value })}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="删除参数"
                  onClick={() => setParamRows((rows) => rows.filter((item) => item.id !== row.id))}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>

          {(formError || upsert.error) && (
            <p className="text-sm text-destructive">{formError ?? upsert.error?.message}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={upsert.isPending}>
            {upsert.isPending ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
