/**
 * 大脑映射卡：fast/smart/cheap/vision 四槽位各一个模型下拉，
 * 逐槽位保存（PUT /v1/brain-mappings，env=当前，默认 dev）。
 * API 无映射查询端点，初始为空选择，保存后即时生效。
 */

import { useState } from 'react';

import {
  useModels,
  useSetBrainMapping,
  type BrainProfile,
  type ModelDefinitionOut,
} from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const CURRENT_ENV = 'dev';

const PROFILE_SLOTS: Array<{ profile: BrainProfile; hint: string }> = [
  { profile: 'fast', hint: '低时延响应' },
  { profile: 'smart', hint: '主力推理（默认槽位）' },
  { profile: 'cheap', hint: '成本敏感、批量任务' },
  { profile: 'vision', hint: '多模态识图' },
];

function ModelSlotSelect({
  profile,
  models,
  value,
  onChange,
}: {
  profile: BrainProfile;
  models: ModelDefinitionOut[];
  value: string;
  onChange: (modelKey: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={`大脑槽位 ${profile}`} className="w-56 font-mono text-xs">
        <SelectValue placeholder="选择模型" />
      </SelectTrigger>
      <SelectContent>
        {models.map((model) => (
          <SelectItem key={model.key} value={model.key} className="font-mono text-xs">
            {model.key}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function BrainMappingCard() {
  const modelsQuery = useModels();
  const setMapping = useSetBrainMapping();
  const [selection, setSelection] = useState<Partial<Record<BrainProfile, string>>>({});
  const [savedProfile, setSavedProfile] = useState<BrainProfile | null>(null);

  const models = modelsQuery.data ?? [];

  function handleSave(profile: BrainProfile): void {
    const modelKey = selection[profile];
    if (!modelKey) return;
    setMapping.mutate(
      { env: CURRENT_ENV, profile, model_key: modelKey },
      { onSuccess: () => setSavedProfile(profile) },
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">大脑映射</CardTitle>
          <Badge variant="outline" className="font-mono text-xs">
            env: {CURRENT_ENV}
          </Badge>
        </div>
        <CardDescription>
          四槽位解耦 Agent 与具体模型——切换模型 =
          改映射，不改代码不发版。映射按环境隔离，保存即时生效。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {PROFILE_SLOTS.map(({ profile, hint }) => (
          <div key={profile} className="flex flex-wrap items-center gap-3">
            <div className="w-40">
              <span className="font-mono text-sm font-medium">{profile}</span>
              <span className="ml-2 text-xs text-muted-foreground">{hint}</span>
            </div>
            <ModelSlotSelect
              profile={profile}
              models={models}
              value={selection[profile] ?? ''}
              onChange={(modelKey) => {
                setSelection((prev) => ({ ...prev, [profile]: modelKey }));
                setSavedProfile((prev) => (prev === profile ? null : prev));
              }}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!selection[profile] || setMapping.isPending}
              onClick={() => handleSave(profile)}
            >
              保存 {profile} 映射
            </Button>
            {savedProfile === profile && <span className="text-xs text-emerald-500">已保存</span>}
          </div>
        ))}
        {setMapping.error && <p className="text-sm text-destructive">{setMapping.error.message}</p>}
      </CardContent>
    </Card>
  );
}
