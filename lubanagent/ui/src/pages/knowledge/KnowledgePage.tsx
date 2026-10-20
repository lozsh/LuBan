/**
 * /knowledge 知识库管理：知识源列表 + 多格式文档上传 + 切块查看 + 重新切块 + 检索测试。
 * 上传经 POST /v1/knowledge/sources（multipart，支持 md/txt/pdf/docx/csv/json/html/xml），
 * 文件落盘 data/knowledge/{name}/，切块向量化入 KnowledgeStore；重切按新参数从盘上重读；
 * 检索测试直接调 POST /v1/knowledge/query 看命中块与 score（调 Agent 阈值用）。
 */

import { Search, Trash2, Upload } from 'lucide-react';
import { useState } from 'react';

import {
  useDeleteKnowledgeSource,
  useKnowledgeChunks,
  useKnowledgeQuery,
  useKnowledgeSources,
  useRechunkKnowledgeSource,
  useUploadKnowledgeSource,
  type KnowledgeSourceOut,
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';

const SOURCE_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/;

function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  return value.replace('T', ' ').replace(/:\d+\.\d+Z?$/, '');
}

export default function KnowledgePage() {
  const sourcesQuery = useKnowledgeSources();
  const delSource = useDeleteKnowledgeSource();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [viewingChunks, setViewingChunks] = useState<KnowledgeSourceOut | null>(null);
  const [rechunking, setRechunking] = useState<KnowledgeSourceOut | null>(null);

  const sources = sourcesQuery.data ?? [];

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">知识库</h1>
          <p className="text-sm text-muted-foreground">
            知识源清单；上传多格式文档建知识源，Agent 在 YAML knowledge.sources 里引用即可检索。
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Upload className="mr-1.5 h-4 w-4" />
          上传文档
        </Button>
      </div>

      {sourcesQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {sourcesQuery.error && (
        <p className="text-sm text-destructive">加载失败：{sourcesQuery.error.message}</p>
      )}

      {sourcesQuery.isSuccess && (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>文件数</TableHead>
                <TableHead>块数</TableHead>
                <TableHead>切块参数</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead className="w-52">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sources.map((source) => (
                <TableRow key={source.name}>
                  <TableCell className="font-mono text-xs font-medium">{source.name}</TableCell>
                  <TableCell className="max-w-56 truncate text-sm text-muted-foreground">
                    {source.description || '—'}
                  </TableCell>
                  <TableCell className="text-sm">{source.files?.length ?? 0}</TableCell>
                  <TableCell className="text-sm">
                    <Badge variant="outline">{source.chunk_count}</Badge>
                  </TableCell>
                  <TableCell className="text-sm">
                    {source.managed ? (
                      <span className="font-mono text-xs">
                        {source.chunk_size}/{source.overlap}
                      </span>
                    ) : (
                      <Badge variant="secondary">内置</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatDate(source.updated_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setViewingChunks(source)}
                      >
                        切块
                      </Button>
                      {source.managed && (
                        <>
                          <Button variant="ghost" size="sm" onClick={() => setRechunking(source)}>
                            重新切块
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={delSource.isPending}
                            onClick={() => {
                              if (window.confirm(`删除知识源「${source.name}」？块与落盘文件将全部清除。`))
                                delSource.mutate(source.name);
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {sources.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-sm text-muted-foreground">
                    暂无知识源，点右上角上传文档创建。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <RetrievalTestPanel sources={sources} />

      <UploadSourceDialog open={uploadOpen} onOpenChange={setUploadOpen} />
      <ChunksDialog source={viewingChunks} onClose={() => setViewingChunks(null)} />
      <RechunkDialog source={rechunking} onClose={() => setRechunking(null)} />
    </div>
  );
}

function UploadSourceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const upload = useUploadKnowledgeSource();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [chunkSize, setChunkSize] = useState(500);
  const [overlap, setOverlap] = useState(50);
  const [files, setFiles] = useState<File[]>([]);

  const nameValid = SOURCE_NAME_PATTERN.test(name);
  const paramsValid = chunkSize > 0 && overlap >= 0 && overlap < chunkSize;
  const valid = nameValid && paramsValid && files.length > 0;

  const close = () => {
    onOpenChange(false);
    setName('');
    setDescription('');
    setChunkSize(500);
    setOverlap(50);
    setFiles([]);
  };

  const submit = () => {
    if (!valid) return;
    upload.mutate(
      { name, description, chunk_size: chunkSize, overlap, files },
      { onSuccess: close },
    );
  };

  return (
    <Dialog open={open} onOpenChange={(v) => (v ? onOpenChange(v) : close())}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>上传文档创建知识源</DialogTitle>
          <DialogDescription>
            支持 md / txt / pdf / docx / csv / json / html / xml；文件落盘后可随时按新参数重新切块。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="ks-name">知识源名</Label>
            <Input
              id="ks-name"
              placeholder="如 team-wiki（2-64 位小写字母/数字/-/_）"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            {name !== '' && !nameValid && (
              <p className="text-xs text-destructive">
                名称须为 2-64 位小写字母/数字/-/_，且以字母或数字开头
              </p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ks-desc">描述（可选）</Label>
            <Input
              id="ks-desc"
              placeholder="这个知识源放什么内容"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="ks-chunk">切块大小（字符）</Label>
              <Input
                id="ks-chunk"
                type="number"
                min={1}
                value={chunkSize}
                onChange={(e) => setChunkSize(Number(e.target.value))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ks-overlap">相邻块重叠（字符）</Label>
              <Input
                id="ks-overlap"
                type="number"
                min={0}
                value={overlap}
                onChange={(e) => setOverlap(Number(e.target.value))}
              />
            </div>
          </div>
          {!paramsValid && (
            <p className="text-xs text-destructive">重叠必须小于切块大小</p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="ks-files">文档文件（可多选）</Label>
            <input
              id="ks-files"
              type="file"
              multiple
              accept=".md,.txt,.pdf,.docx,.csv,.json,.html,.xml"
              className="text-sm"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            {files.length > 0 && (
              <p className="text-xs text-muted-foreground">
                已选 {files.length} 个文件：{files.map((f) => f.name).join('、')}
              </p>
            )}
          </div>
        </div>
        {upload.error && <p className="text-sm text-destructive">{upload.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={close}>
            取消
          </Button>
          <Button disabled={!valid || upload.isPending} onClick={submit}>
            {upload.isPending ? '上传中…' : '上传'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ChunksDialog({
  source,
  onClose,
}: {
  source: KnowledgeSourceOut | null;
  onClose: () => void;
}) {
  const chunksQuery = useKnowledgeChunks(source?.name ?? '');
  const chunks = chunksQuery.data ?? [];

  return (
    <Dialog open={source !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-mono text-base">{source?.name} 切块</DialogTitle>
          <DialogDescription>
            共 {source?.chunk_count ?? 0} 块（展示前 {Math.min(chunks.length, 200)} 块）
          </DialogDescription>
        </DialogHeader>
        {chunksQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
        {chunksQuery.error && (
          <p className="text-sm text-destructive">加载失败：{chunksQuery.error.message}</p>
        )}
        {chunksQuery.isSuccess && (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">#</TableHead>
                  <TableHead>内容</TableHead>
                  <TableHead className="w-32">来源文件</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {chunks.map((chunk) => (
                  <TableRow key={chunk.chunk_index}>
                    <TableCell className="font-mono text-xs">{chunk.chunk_index}</TableCell>
                    <TableCell
                      className="max-w-96 truncate text-sm"
                      title={chunk.content}
                    >
                      {chunk.content}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {String(chunk.metadata?.filename ?? '—')}
                    </TableCell>
                  </TableRow>
                ))}
                {chunks.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="text-center text-sm text-muted-foreground">
                      无切块（源为空或未灌库）
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function RechunkDialog({
  source,
  onClose,
}: {
  source: KnowledgeSourceOut | null;
  onClose: () => void;
}) {
  const rechunk = useRechunkKnowledgeSource(source?.name ?? '');
  const [chunkSize, setChunkSize] = useState(0);
  const [overlap, setOverlap] = useState(0);
  const [initialized, setInitialized] = useState('');

  // 对话框每次打开按源当前参数预填（initialized 记 source.name 防止重复初始化）
  if (source && initialized !== source.name) {
    setChunkSize(source.chunk_size ?? 500);
    setOverlap(source.overlap ?? 50);
    setInitialized(source.name);
  }

  const paramsValid = chunkSize > 0 && overlap >= 0 && overlap < chunkSize;

  const submit = () => {
    if (!source || !paramsValid) return;
    if (
      !window.confirm(
        `将按新参数（${chunkSize}/${overlap}）从已落盘文件重新切块与向量化「${source.name}」，现有块会被覆盖。继续？`,
      )
    )
      return;
    rechunk.mutate({ chunk_size: chunkSize, overlap }, { onSuccess: onClose });
  };

  return (
    <Dialog open={source !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>重新切块「{source?.name}」</DialogTitle>
          <DialogDescription>
            将按新参数从已落盘文件重新切块与向量化（覆盖现有块）。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="rc-chunk">切块大小（字符）</Label>
            <Input
              id="rc-chunk"
              type="number"
              min={1}
              value={chunkSize}
              onChange={(e) => setChunkSize(Number(e.target.value))}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rc-overlap">相邻块重叠（字符）</Label>
            <Input
              id="rc-overlap"
              type="number"
              min={0}
              value={overlap}
              onChange={(e) => setOverlap(Number(e.target.value))}
            />
          </div>
        </div>
        {!paramsValid && <p className="text-xs text-destructive">重叠必须小于切块大小</p>}
        {rechunk.error && <p className="text-sm text-destructive">{rechunk.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button disabled={!paramsValid || rechunk.isPending} onClick={submit}>
            {rechunk.isPending ? '重新切块中…' : '重新切块'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RetrievalTestPanel({ sources }: { sources: KnowledgeSourceOut[] }) {
  const queryMutation = useKnowledgeQuery();
  const [source, setSource] = useState('all');
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const hits = queryMutation.data ?? [];

  const submit = () => {
    if (!query.trim()) return;
    queryMutation.mutate({
      query: query.trim(),
      source: source === 'all' ? null : source,
      top_k: topK,
    });
  };

  return (
    <div className="rounded-md border p-4">
      <h2 className="mb-1 text-sm font-semibold">检索测试</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        输入查询看命中哪些块与相似度分数，用于校准 Agent YAML 里的 score_threshold。
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56 space-y-1.5">
          <Label htmlFor="rt-source">知识源</Label>
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger id="rt-source">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部知识源</SelectItem>
              {sources.map((s) => (
                <SelectItem key={s.name} value={s.name}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-64 flex-1 space-y-1.5">
          <Label htmlFor="rt-query">查询</Label>
          <Textarea
            id="rt-query"
            placeholder="如：退货政策是什么？"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="w-24 space-y-1.5">
          <Label htmlFor="rt-topk">top_k</Label>
          <Input
            id="rt-topk"
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
          />
        </div>
        <Button
          disabled={!query.trim() || queryMutation.isPending}
          onClick={submit}
        >
          <Search className="mr-1.5 h-4 w-4" />
          {queryMutation.isPending ? '检索中…' : '检索'}
        </Button>
      </div>
      {queryMutation.error && (
        <p className="mt-3 text-sm text-destructive">检索失败：{queryMutation.error.message}</p>
      )}
      {queryMutation.isSuccess && (
        <div className="mt-3 space-y-2">
          {hits.length === 0 && (
            <p className="text-sm text-muted-foreground">无命中块</p>
          )}
          {hits.map((hit) => (
            <div key={`${hit.source}-${hit.chunk_index}`} className="rounded-md border p-2">
              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline">{hit.score.toFixed(3)}</Badge>
                <span className="font-mono">{hit.source}</span>
                <span>#{hit.chunk_index}</span>
                {hit.metadata?.filename ? <span>{String(hit.metadata.filename)}</span> : null}
              </div>
              <p className="text-sm">{hit.content}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
