/**
 * 新建 Agent 对话框：key/name/description/instructions + 大脑槽位 → POST /v1/agents。
 * 成功后跳到该 Agent 的编辑器。校验只做 UX 层（必填/key 格式），冲突等业务错误展示 error.message。
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useCreateAgent, type BrainProfile } from '@/api/hooks';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';

import { BRAIN_PROFILES, emptyAgentDefinition } from '../lib/agent-form';

const KEY_PATTERN = /^[a-z0-9][a-z0-9-_]*$/;

interface CreateAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateAgentDialog({ open, onOpenChange }: CreateAgentDialogProps) {
  const navigate = useNavigate();
  const createAgent = useCreateAgent();

  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [profile, setProfile] = useState<BrainProfile>('smart');
  const [formError, setFormError] = useState<string | null>(null);

  const reset = () => {
    setKey('');
    setName('');
    setDescription('');
    setInstructions('');
    setProfile('smart');
    setFormError(null);
    createAgent.reset();
  };

  const submit = () => {
    if (key.trim() === '' || name.trim() === '') {
      setFormError('key 与名称为必填项');
      return;
    }
    if (!KEY_PATTERN.test(key.trim())) {
      setFormError('key 只能包含小写字母、数字、中划线与下划线');
      return;
    }
    setFormError(null);
    const definition = emptyAgentDefinition(
      key.trim(),
      name.trim(),
      description.trim(),
      instructions,
      profile,
    );
    createAgent.mutate(definition, {
      onSuccess: (created) => {
        onOpenChange(false);
        reset();
        navigate(`/agents/${created.key}`);
      },
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>新建 Agent</DialogTitle>
          <DialogDescription>
            创建草稿定义（v1），创建后进入编辑器补全能力并保存版本。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="new-agent-key">Key（唯一，创建后不可改）</Label>
              <Input
                id="new-agent-key"
                value={key}
                placeholder="如 cs-agent"
                className="font-mono"
                onChange={(event) => setKey(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-agent-name">名称</Label>
              <Input
                id="new-agent-name"
                value={name}
                placeholder="如 智能客服"
                onChange={(event) => setName(event.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-agent-description">描述</Label>
            <Input
              id="new-agent-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-agent-instructions">系统指令（instructions）</Label>
            <Textarea
              id="new-agent-instructions"
              value={instructions}
              rows={5}
              className="font-mono text-sm"
              onChange={(event) => setInstructions(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>逻辑大脑槽位</Label>
            <Select value={profile} onValueChange={(value) => setProfile(value as BrainProfile)}>
              <SelectTrigger aria-label="逻辑大脑槽位">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BRAIN_PROFILES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          {createAgent.error && (
            <p className="text-sm text-destructive">{createAgent.error.message}</p>
          )}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button type="button" disabled={createAgent.isPending} onClick={submit}>
            {createAgent.isPending ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
