/**
 * /skills Skill 管理：列表 + 上传 zip 包 + 分配到 Agent + manifest 查看。
 * 上传 zip 经 POST /v1/skills 安装到 skills_root/{name}/；分配经
 * POST /v1/agents/{key}/versions 改 agent.skills（运行期 make_load_tool
 * 强制按分配加载，未分配的 Skill 调 skill_load 被 403 拒绝）。
 */

import { FileJson, Share2, Trash2, Upload } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  useAgents,
  useAssignSkillToAgents,
  useDeleteSkill,
  useSkills,
  useUploadSkill,
  type AgentDefinition,
  type SkillOut,
} from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

function manifestDescription(manifest: Record<string, unknown> | undefined): string {
  const description = manifest?.description;
  return typeof description === 'string' ? description : '—';
}

export default function SkillsPage() {
  const skillsQuery = useSkills();
  const agentsQuery = useAgents();
  const delSkill = useDeleteSkill();
  const [viewing, setViewing] = useState<SkillOut | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [assigning, setAssigning] = useState<SkillOut | null>(null);

  const skills = skillsQuery.data ?? [];
  const agents = useMemo(() => agentsQuery.data ?? [], [agentsQuery.data]);

  // 反查：skill key → 已分配的 Agent 列表
  const assignmentMap = useMemo(() => {
    const m = new Map<string, AgentDefinition[]>();
    for (const a of agents) {
      for (const s of a.skills ?? []) {
        if (!m.has(s)) m.set(s, []);
        m.get(s)!.push(a);
      }
    }
    return m;
  }, [agents]);

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Skill 管理</h1>
          <p className="text-sm text-muted-foreground">
            Skill 包清单；上传 zip 安装新包，按 Skill 分配给 Agent（运行期强制按分配加载）。
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Upload className="mr-1.5 h-4 w-4" />
          上传 Skill 包
        </Button>
      </div>

      {skillsQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {skillsQuery.error && (
        <p className="text-sm text-destructive">加载失败：{skillsQuery.error.message}</p>
      )}

      {skillsQuery.isSuccess && (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>版本</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>已分配 Agent</TableHead>
                <TableHead className="w-44">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {skills.map((skill) => {
                const assigned = assignmentMap.get(skill.key) ?? [];
                return (
                  <TableRow key={skill.key}>
                    <TableCell className="text-sm font-medium">{skill.name}</TableCell>
                    <TableCell className="font-mono text-xs">{skill.key}</TableCell>
                    <TableCell className="font-mono text-xs">{skill.version}</TableCell>
                    <TableCell className="max-w-72 truncate text-sm text-muted-foreground">
                      {manifestDescription(skill.manifest)}
                    </TableCell>
                    <TableCell className="text-sm">
                      {assigned.length === 0 ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <Badge variant="outline">{assigned.length}</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setViewing(skill)}>
                          <FileJson className="mr-1 h-3.5 w-3.5" />
                          manifest
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setAssigning(skill)}>
                          <Share2 className="mr-1 h-3.5 w-3.5" />
                          分配
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={delSkill.isPending}
                          onClick={() => {
                            if (window.confirm(`删除 Skill「${skill.key}」？`))
                              delSkill.mutate(skill.key);
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
              {skills.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-center text-sm text-muted-foreground">
                    暂无 Skill，点右上角上传一个 zip 包。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <UploadSkillDialog open={uploadOpen} onOpenChange={setUploadOpen} />
      <AssignSkillDialog skill={assigning} agents={agents} onClose={() => setAssigning(null)} />

      <Dialog open={viewing !== null} onOpenChange={(open) => !open && setViewing(null)}>
        <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-mono text-base">{viewing?.key} manifest</DialogTitle>
            <DialogDescription>SkillPackage 定义（只读）</DialogDescription>
          </DialogHeader>
          <pre className="rounded-md border bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap">
            {viewing ? JSON.stringify(viewing.manifest ?? {}, null, 2) : ''}
          </pre>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function UploadSkillDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const upload = useUploadSkill();
  const [file, setFile] = useState<File | null>(null);

  const submit = () => {
    if (!file) return;
    upload.mutate(file, {
      onSuccess: () => {
        onOpenChange(false);
        setFile(null);
      },
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        onOpenChange(v);
        if (!v) setFile(null);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>上传 Skill 包</DialogTitle>
          <DialogDescription>
            zip 包内须含一个目录，目录下有 SKILL.md（YAML frontmatter: name/description/version），
            可附带 scripts/references/assets 子目录。同名 Skill 会覆盖。
          </DialogDescription>
        </DialogHeader>
        <input
          type="file"
          accept=".zip"
          className="text-sm"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {upload.error && <p className="text-sm text-destructive">{upload.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={!file || upload.isPending} onClick={submit}>
            {upload.isPending ? '上传中…' : '上传'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function AssignSkillDialog({
  skill,
  agents,
  onClose,
}: {
  skill: SkillOut | null;
  agents: AgentDefinition[];
  onClose: () => void;
}) {
  const assign = useAssignSkillToAgents();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (skill) {
      setSelected(
        new Set(
          agents.filter((a) => (a.skills ?? []).includes(skill.key)).map((a) => a.key),
        ),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skill?.key, agents]);

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const submit = () => {
    if (!skill) return;
    const targets: Record<string, boolean> = {};
    for (const k of selected) targets[k] = true;
    assign.mutate({ skillKey: skill.key, targets, agents }, { onSuccess: () => onClose() });
  };

  if (!skill) return null;

  return (
    <Dialog open={skill !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>分配 Skill「{skill.key}」到 Agent</DialogTitle>
          <DialogDescription>
            勾选要分配的 Agent；保存即为每个变化的 Agent 产生新版本（skills 数组增删本 Skill）。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-80 space-y-1 overflow-y-auto">
          {agents.map((a) => (
            <label
              key={a.key}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted/50"
            >
              <input
                type="checkbox"
                className="accent-primary"
                checked={selected.has(a.key)}
                onChange={() => toggle(a.key)}
              />
              <span className="font-medium">{a.name}</span>
              <span className="font-mono text-xs text-muted-foreground">{a.key}</span>
              <span className="ml-auto text-xs text-muted-foreground">v{a.version}</span>
            </label>
          ))}
        </div>
        {assign.error && <p className="text-sm text-destructive">{assign.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button disabled={assign.isPending} onClick={submit}>
            {assign.isPending ? '保存中…' : '保存分配'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
