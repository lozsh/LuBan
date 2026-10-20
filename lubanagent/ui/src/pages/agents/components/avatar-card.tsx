/**
 * Agent 形象卡（编辑器「身份」Tab 内）：当前形象预览 + 上传/移除。
 * 立即生效、独立于表单草稿——服务端上传即追加新版本（PUT /v1/agents/{key}/avatar），
 * 成功后把返回的 version/avatar 回写进编辑器草稿（防后续保存撞"版本已存在" 409），
 * 其余草稿改动不受影响地保留在表单里。
 */

import { useRef, useState } from 'react';

import { useRemoveAvatar, useUploadAvatar, type AgentDefinition } from '@/api/hooks';
import { AgentAvatar } from '@/components/agent-avatar';
import { Button } from '@/components/ui/button';

export interface AvatarCardProps {
  agent: AgentDefinition;
  /** 上传/移除成功后回写草稿（version/avatar 由服务端新版本决定） */
  onSynced: (patch: Pick<AgentDefinition, 'version' | 'avatar'>) => void;
}

export function AvatarCard({ agent, onSynced }: AvatarCardProps) {
  const upload = useUploadAvatar();
  const remove = useRemoveAvatar();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = (picked: File | null) => {
    setFile(picked);
    setError(null);
  };

  const doUpload = () => {
    if (!file) return;
    setError(null);
    upload.mutate(
      { key: agent.key, file },
      {
        onSuccess: (definition) => {
          setFile(null);
          if (inputRef.current) inputRef.current.value = '';
          onSynced({ version: definition.version, avatar: definition.avatar });
        },
        onError: (apiError) => setError(apiError.message),
      },
    );
  };

  const doRemove = () => {
    setError(null);
    remove.mutate(
      { key: agent.key },
      {
        onSuccess: () => onSynced({ version: agent.version + 1, avatar: null }),
        onError: (apiError) => setError(apiError.message),
      },
    );
  };

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-lg border p-3"
      data-testid="avatar-card"
    >
      <AgentAvatar agent={agent} size={48} />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">虚拟形象</div>
        <div className="text-xs text-muted-foreground">
          SVG / PNG / GLB / GLTF，≤ 20MB。上传立即生效并生成新版本（不随表单草稿）。
          {agent.avatar && <span className="ml-1 font-mono">{agent.avatar}</span>}
        </div>
        {error && <div className="mt-1 text-xs text-destructive">{error}</div>}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".svg,.png,.glb,.gltf"
        className="text-xs"
        aria-label="形象文件"
        onChange={(event) => pick(event.target.files?.[0] ?? null)}
      />
      <Button size="sm" disabled={!file || upload.isPending} onClick={doUpload}>
        {upload.isPending ? '上传中…' : '上传'}
      </Button>
      {agent.avatar && (
        <Button size="sm" variant="outline" disabled={remove.isPending} onClick={doRemove}>
          移除
        </Button>
      )}
    </div>
  );
}
