/** 可编辑槽位目录与按环境隔离的模型映射；删除保护由后端最终裁决。 */

import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useState } from 'react';

import {
  useBrainMappings,
  useBrainProfileReferences,
  useBrainProfiles,
  useCreateBrainProfile,
  useDeleteBrainProfile,
  useModels,
  useSetBrainMapping,
  useUpdateBrainProfile,
  type BrainProfile,
  type BrainProfileOut,
  type BrainProfileReference,
  type ModelDefinitionOut,
} from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { usePermissions } from '@/lib/permissions';

const PROFILE_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;
const REFERENCE_FIELDS: Record<string, string> = {
  'brain.profile': '主槽位',
  'brain.fallback_profiles': '降级槽位',
  'memory.summary': '记忆摘要',
};

function ModelSlotSelect({
  profile,
  models,
  value,
  onChange,
  disabled = false,
}: {
  profile: BrainProfile;
  models: ModelDefinitionOut[];
  value: string;
  onChange: (modelKey: string) => void;
  disabled?: boolean;
}) {
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={`大脑槽位 ${profile}`} className="w-56 font-mono text-xs">
        <SelectValue placeholder="选择模型" />
      </SelectTrigger>
      <SelectContent>
        {value && !models.some((model) => model.key === value) && (
          <SelectItem value={value}>{value}（当前映射，不可用）</SelectItem>
        )}
        {models.map((model) => (
          <SelectItem key={model.key} value={model.key} className="font-mono text-xs">
            {model.key}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function BrainProfileDialog({
  initial,
  models,
  modelKey,
  env,
  onClose,
}: {
  initial: BrainProfileOut | null;
  models: ModelDefinitionOut[];
  modelKey: string;
  env: string;
  onClose: () => void;
}) {
  const create = useCreateBrainProfile();
  const update = useUpdateBrainProfile();
  const [key, setKey] = useState(initial?.key ?? '');
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [model, setModel] = useState(modelKey || 'unmapped');
  const [formError, setFormError] = useState('');
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;

  function submit(): void {
    if (!PROFILE_PATTERN.test(key.trim()) || !name.trim()) {
      setFormError(
        '名称不能为空；标识须以小写字母开头，只含小写字母、数字、下划线或中划线，最多 64 个字符。',
      );
      return;
    }
    setFormError('');
    const body = {
      key: key.trim(),
      name: name.trim(),
      description: description.trim(),
      env,
      ...(model !== 'unmapped' && model !== modelKey ? { model_key: model } : {}),
    };
    if (initial) update.mutate({ key: initial.key, patch: body }, { onSuccess: onClose });
    else create.mutate(body, { onSuccess: onClose });
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{initial ? '编辑大脑槽位' : '新增大脑槽位'}</DialogTitle>
          <DialogDescription>
            名称用于页面展示，标识用于 Agent 配置。改名保留旧标识兼容已有定义，模型映射按环境保存。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="brain-slot-name">槽位名称</Label>
              <Input
                id="brain-slot-name"
                value={name}
                maxLength={128}
                disabled={pending}
                placeholder="例如：客服推理"
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="brain-slot-key">槽位标识</Label>
              <Input
                id="brain-slot-key"
                value={key}
                maxLength={64}
                disabled={pending}
                className="font-mono"
                placeholder="例如：customer_reasoning"
                onChange={(event) => setKey(event.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="brain-slot-description">用途说明</Label>
            <Textarea
              id="brain-slot-description"
              value={description}
              maxLength={512}
              disabled={pending}
              rows={3}
              placeholder="这个槽位负责什么任务"
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>映射模型（{env}）</Label>
            <Select value={model} onValueChange={setModel} disabled={pending}>
              <SelectTrigger aria-label="槽位映射模型">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unmapped" disabled={!!modelKey}>
                  {modelKey ? '保留现有映射' : '暂不映射，稍后配置'}
                </SelectItem>
                {modelKey && !models.some((item) => item.key === modelKey) && (
                  <SelectItem value={modelKey}>{modelKey}（当前映射，不可用）</SelectItem>
                )}
                {models.map((item) => (
                  <SelectItem key={item.key} value={item.key}>
                    {item.key}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {(formError || error) && (
            <p role="alert" className="text-sm text-destructive">
              {formError || error?.message}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={pending} onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? '保存中…' : '保存槽位'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteBrainProfileDialog({
  slot,
  onClose,
}: {
  slot: BrainProfileOut;
  onClose: () => void;
}) {
  const query = useBrainProfileReferences(slot.key);
  const remove = useDeleteBrainProfile();
  // 查询后又有 Agent 保存时，以 DELETE 409 返回的引用为准，不能只相信弹窗预检。
  const conflictRefs = remove.error?.details.references as BrainProfileReference[] | undefined;
  const references = conflictRefs ?? query.data?.references ?? [];
  const used = references.length > 0;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !remove.isPending) onClose();
      }}
    >
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{used ? '槽位正在使用，不能删除' : '删除大脑槽位'}</DialogTitle>
          <DialogDescription>
            {slot.name}（{slot.key}）：会检查所有 Agent
            的主槽位、降级槽位和记忆摘要，包括仍可运行的历史版本。
          </DialogDescription>
        </DialogHeader>
        {query.isFetching && <p className="text-sm text-muted-foreground">正在检查使用情况…</p>}
        {used ? (
          <ul
            aria-label="使用此槽位的 Agent"
            className="max-h-64 space-y-2 overflow-y-auto rounded-md border p-3"
          >
            {references.map((ref) => (
              <li
                key={`${ref.agent_key}-${ref.version}-${ref.field}`}
                className="flex flex-wrap items-center gap-2 text-sm"
              >
                <span className="font-medium">{ref.agent_name}</span>
                <span className="font-mono text-xs text-muted-foreground">
                  {ref.agent_key} · v{ref.version}
                </span>
                <Badge variant="outline">{ref.current ? '当前版本' : '历史版本'}</Badge>
                <span className="text-xs text-muted-foreground">
                  {REFERENCE_FIELDS[ref.field] ?? ref.field}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          query.isSuccess && (
            <p className="text-sm">没有 Agent 使用。删除会移除此槽位及所有环境的模型映射。</p>
          )
        )}
        {(query.error || remove.error) && (
          <p role="alert" className="text-sm text-destructive">
            {remove.error?.message ?? query.error?.message}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={remove.isPending} onClick={onClose}>
            {used ? '关闭' : '取消'}
          </Button>
          <Button
            variant="destructive"
            disabled={!query.isSuccess || query.isFetching || used || remove.isPending}
            onClick={() => remove.mutate(slot.key, { onSuccess: onClose })}
          >
            {remove.isPending ? '删除中…' : '确认删除槽位'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function BrainMappingCard() {
  const modelsQuery = useModels();
  const profilesQuery = useBrainProfiles();
  const env = profilesQuery.data?.env ?? '';
  const mappingsQuery = useBrainMappings(env);
  const { me, can } = usePermissions();
  const canWrite = can('config:write');
  const setMapping = useSetBrainMapping();
  const [selection, setSelection] = useState<Record<string, string>>({});
  const [savedProfile, setSavedProfile] = useState<BrainProfile | null>(null);
  const [editing, setEditing] = useState<BrainProfileOut | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<BrainProfileOut | null>(null);

  const models = modelsQuery.data ?? [];
  const slots = profilesQuery.data?.items ?? [];
  const mapped = Object.fromEntries(
    (mappingsQuery.data ?? []).map((mapping) => [mapping.profile, mapping.model_key]),
  );

  function handleSave(profile: BrainProfile): void {
    const modelKey = selection[profile] ?? mapped[profile];
    if (!modelKey || !env) return;
    setMapping.mutate(
      { env, profile, model_key: modelKey },
      {
        onSuccess: () => {
          setSavedProfile(profile);
          setSelection((previous) => {
            if (previous[profile] !== modelKey) return previous;
            const next = { ...previous };
            delete next[profile];
            return next;
          });
        },
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">大脑映射</CardTitle>
            <Badge variant="outline" className="font-mono text-xs">
              env: {env || '加载中'}
            </Badge>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={!canWrite || !env}
            onClick={() => setEditing(null)}
          >
            <Plus className="mr-1 h-4 w-4" />
            新增槽位
          </Button>
        </div>
        <CardDescription>
          槽位是模型的业务名称，可新增、改名和编辑用途。切换模型只改映射，保存即时生效；有 Agent
          使用时不能删除。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {profilesQuery.isLoading && <p className="text-sm text-muted-foreground">加载槽位…</p>}
        {profilesQuery.error && (
          <p role="alert" className="text-sm text-destructive">
            加载失败：{profilesQuery.error.message}
          </p>
        )}
        {mappingsQuery.error && (
          <p role="alert" className="text-sm text-destructive">
            映射加载失败：{mappingsQuery.error.message}
          </p>
        )}
        {profilesQuery.isSuccess && slots.length === 0 && (
          <p className="text-sm text-muted-foreground">暂无槽位，点击「新增槽位」开始配置。</p>
        )}
        {slots.map((slot) => (
          <div
            key={slot.key}
            data-brain-profile={slot.key}
            className="flex flex-wrap items-center gap-3 rounded-md border px-4 py-3"
          >
            <div className="min-w-40 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{slot.name}</span>
                {slot.name !== slot.key && (
                  <span className="font-mono text-xs text-muted-foreground">{slot.key}</span>
                )}
                {slot.agent_count > 0 && (
                  <Badge variant="outline">{slot.agent_count} 个 Agent 使用</Badge>
                )}
              </div>
              {slot.description && (
                <p className="mt-1 text-xs text-muted-foreground">{slot.description}</p>
              )}
              {slot.aliases.length > 0 && (
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  兼容旧标识：{slot.aliases.join(', ')}
                </p>
              )}
            </div>
            <ModelSlotSelect
              profile={slot.key}
              models={models}
              value={selection[slot.key] ?? mapped[slot.key] ?? ''}
              disabled={!canWrite || !mappingsQuery.isSuccess}
              onChange={(modelKey) => {
                setSelection((prev) => ({ ...prev, [slot.key]: modelKey }));
                setSavedProfile(null);
              }}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={
                !canWrite ||
                !(selection[slot.key] ?? mapped[slot.key]) ||
                !mappingsQuery.isSuccess ||
                setMapping.isPending
              }
              onClick={() => handleSave(slot.key)}
            >
              保存 {slot.key} 映射
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label={`编辑槽位 ${slot.key}`}
              disabled={!canWrite || !mappingsQuery.isSuccess}
              onClick={() => setEditing(slot)}
            >
              <Pencil className="h-4 w-4" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              aria-label={`删除槽位 ${slot.key}`}
              disabled={!canWrite}
              onClick={() => setDeleting(slot)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
            {savedProfile === slot.key && <span className="text-xs text-emerald-500">已保存</span>}
          </div>
        ))}
        {setMapping.error && <p className="text-sm text-destructive">{setMapping.error.message}</p>}
        {me && !canWrite && (
          <p className="text-xs text-muted-foreground">
            当前角色只有查看权限，修改需要 config:write。
          </p>
        )}
      </CardContent>
      {editing !== undefined && (
        <BrainProfileDialog
          key={editing?.key ?? 'new'}
          initial={editing}
          models={models}
          modelKey={editing ? (mapped[editing.key] ?? '') : ''}
          env={env}
          onClose={() => setEditing(undefined)}
        />
      )}
      {deleting && <DeleteBrainProfileDialog slot={deleting} onClose={() => setDeleting(null)} />}
    </Card>
  );
}
