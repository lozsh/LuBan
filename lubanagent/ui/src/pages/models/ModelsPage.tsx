/**
 * /models 模型中心：模型表（脱敏视图）+ 新建/编辑 Dialog + 大脑映射卡。
 */

import { Plus } from 'lucide-react';
import { useState } from 'react';

import { useModels, type ModelDefinitionOut } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import { BrainMappingCard } from './BrainMappingCard';
import { ModelFormDialog } from './ModelFormDialog';

const CAPABILITY_BADGES = [
  'chat',
  'tool_calling',
  'vision',
  'streaming',
  'structured_output',
  'reasoning',
] as const;

function CapabilityBadges({ model }: { model: ModelDefinitionOut }) {
  const capability = model.capability;
  if (!capability) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {CAPABILITY_BADGES.filter((name) => capability[name]).map((name) => (
        <Badge key={name} variant="outline" className="font-mono text-[10px]">
          {name}
        </Badge>
      ))}
      <span className="text-[10px] text-muted-foreground">
        ctx {capability.context_window} / out {capability.max_output}
      </span>
    </div>
  );
}

function ParamsCell({ params }: { params: Record<string, unknown> | undefined }) {
  const entries = Object.entries(params ?? {});
  if (entries.length === 0) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="font-mono text-xs text-muted-foreground">
      {entries.map(([name, value]) => `${name}=${JSON.stringify(value)}`).join(' ')}
    </span>
  );
}

export default function ModelsPage() {
  const modelsQuery = useModels();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ModelDefinitionOut | null>(null);

  const models = modelsQuery.data ?? [];

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">模型中心</h1>
          <p className="text-sm text-muted-foreground">
            模型注册表（脱敏：只见 api_key_env 变量名，不见连接信息）
          </p>
        </div>
        <Button
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          <Plus className="mr-1 h-4 w-4" />
          新建模型
        </Button>
      </div>

      {modelsQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {modelsQuery.error && (
        <p className="text-sm text-destructive">加载失败：{modelsQuery.error.message}</p>
      )}

      {modelsQuery.isSuccess && (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Key</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>能力</TableHead>
                <TableHead>Params</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="w-20">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model) => (
                <TableRow key={model.key}>
                  <TableCell className="font-mono text-xs font-medium">{model.key}</TableCell>
                  <TableCell className="text-sm">{model.provider}</TableCell>
                  <TableCell className="font-mono text-xs">{model.model_name}</TableCell>
                  <TableCell>
                    <CapabilityBadges model={model} />
                  </TableCell>
                  <TableCell>
                    <ParamsCell params={model.params} />
                  </TableCell>
                  <TableCell>
                    <Badge variant={model.enabled ? 'default' : 'outline'}>
                      {model.enabled ? '启用' : '停用'}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditing(model);
                        setDialogOpen(true);
                      }}
                    >
                      编辑
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {models.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-sm text-muted-foreground">
                    暂无模型，点击右上角「新建模型」注册第一个。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <BrainMappingCard />

      {dialogOpen && (
        <ModelFormDialog
          key={editing?.key ?? 'new'}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          initial={editing}
        />
      )}
    </div>
  );
}
