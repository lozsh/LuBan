/** 日志排查：Run Tab 按执行链筛选；日志 Tab 额外支持请求 ID、时间范围和关键字。 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useDiagnosticRuns, useLogs, type LogEntry } from '@/api/diagnostics';
import { Button } from '@/components/ui/button';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatDateTime } from '@/lib/format';

import { RunStatusBadge } from '@/pages/traces/TracesPage';

const EMPTY_FILTER = {
  session_id: '',
  run_id: '',
  trace_id: '',
  user_id: '',
  status: 'all',
  request_id: '',
  logger: '',
  q: '',
  level: 'all',
  from_time: '',
  to_time: '',
};

const LEVEL_CLASS: Record<LogEntry['level'], string> = {
  DEBUG: 'text-slate-500',
  INFO: 'text-emerald-500',
  WARNING: 'text-amber-500',
  ERROR: 'text-red-500',
  CRITICAL: 'text-red-600',
};

function short(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length > 16 ? `${value.slice(0, 14)}…` : value;
}

/** datetime-local 是本地无时区表示；后端 from_time/to_time 需要 ISO datetime。 */
function toIso(value: string): string | undefined {
  if (value === '') return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export default function DiagnosticsPage() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState(EMPTY_FILTER);
  const [filter, setFilter] = useState(EMPTY_FILTER);
  const [activeTab, setActiveTab] = useState<'runs' | 'logs'>('runs');
  const [runPage, setRunPage] = useState(0);
  const [logPage, setLogPage] = useState(0);
  const [timeError, setTimeError] = useState('');
  const pageSize = 20;

  const { session_id, run_id, trace_id, user_id, status, request_id, logger, q } = filter;
  const fromTime = toIso(filter.from_time);
  const toTime = toIso(filter.to_time);
  const runsQuery = useDiagnosticRuns({
    session_id,
    run_id,
    trace_id,
    user_id,
    status: status === 'all' ? undefined : status,
    limit: pageSize,
    offset: runPage * pageSize,
  });
  const logsQuery = useLogs({
    session_id,
    run_id,
    trace_id,
    request_id,
    logger,
    q,
    level: filter.level === 'all' ? undefined : filter.level,
    from_time: fromTime,
    to_time: toTime,
    limit: pageSize,
    offset: logPage * pageSize,
  });

  const activeTotal = activeTab === 'runs' ? runsQuery.data?.total : logsQuery.data?.total;
  const activePage = activeTab === 'runs' ? runPage : logPage;
  const activeHasPrevious = activePage > 0;
  const activeHasNext = activeTotal !== undefined && (activePage + 1) * pageSize < activeTotal;

  function update(field: keyof typeof EMPTY_FILTER, value: string): void {
    setDraft((prev) => ({ ...prev, [field]: value }));
  }

  function normalizedDraftTime(name: 'from_time' | 'to_time'): string | undefined {
    const raw = draft[name];
    if (raw === '') return undefined;
    const iso = toIso(raw);
    if (!iso) throw new Error('时间格式不正确');
    return iso;
  }

  return (
    <div className="space-y-4 p-4">
      <header>
        <h1 className="text-xl font-semibold">日志排查</h1>
        <p className="text-sm text-muted-foreground">
          Run Tab 支持 session / run / trace / user / status；日志 Tab 额外支持请求 ID、
          级别、Logger、关键字和时间范围。
        </p>
      </header>

      <form
        className="grid gap-3 rounded-md border bg-card/40 p-3 md:grid-cols-3 xl:grid-cols-7"
        onSubmit={(event) => {
          event.preventDefault();
          try {
            const from = normalizedDraftTime('from_time');
            const to = normalizedDraftTime('to_time');
            if (from && to && new Date(from) > new Date(to)) {
              setTimeError('开始时间不能晚于结束时间');
              return;
            }
            setTimeError('');
          } catch {
            setTimeError('时间格式不正确');
            return;
          }
          setRunPage(0);
          setLogPage(0);
          setFilter(draft);
        }}
      >
        {(
          [
            ['session_id', '会话 ID'],
            ['run_id', 'Run ID'],
            ['trace_id', 'Trace ID'],
            ['request_id', '请求 ID'],
            ['logger', 'Logger'],
            ['q', '内容关键字'],
          ] as const
        ).map(([name, label]) => (
          <div key={name} className="space-y-1">
            <Label htmlFor={`diag-${name}`}>{label}</Label>
            <Input
              id={`diag-${name}`}
              value={draft[name]}
              onChange={(event) => update(name, event.target.value)}
            />
          </div>
        ))}
        <div className="space-y-1">
          <Label htmlFor="diag-user_id">用户（仅 Run Tab）</Label>
          <Input
            id="diag-user_id"
            value={draft.user_id}
            onChange={(event) => update('user_id', event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label>状态（仅 Run Tab）</Label>
          <Select value={draft.status} onValueChange={(value) => update('status', value)}>
            <SelectTrigger aria-label="Run 状态">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              {(
                [
                  'pending',
                  'running',
                  'waiting_approval',
                  'completed',
                  'failed',
                  'cancelled',
                ] as const
              ).map((status) => (
                <SelectItem key={status} value={status}>
                  {status}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label>级别</Label>
          <Select value={draft.level} onValueChange={(value) => update('level', value)}>
            <SelectTrigger aria-label="日志级别">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const).map((level) => (
                <SelectItem key={level} value={level}>
                  {level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="diag-from_time">开始时间（仅日志）</Label>
          <Input
            id="diag-from_time"
            type="datetime-local"
            value={draft.from_time}
            onChange={(event) => update('from_time', event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="diag-to_time">结束时间（仅日志）</Label>
          <Input
            id="diag-to_time"
            type="datetime-local"
            value={draft.to_time}
            onChange={(event) => update('to_time', event.target.value)}
          />
        </div>
        <div className="flex items-end md:col-span-3 xl:col-span-7">
          <div className="w-full space-y-2">
            {timeError && <p className="text-sm text-red-500">{timeError}</p>}
            <Button type="submit">查询</Button>
          </div>
        </div>
      </form>

      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as 'runs' | 'logs')}>
        <TabsList>
          <TabsTrigger value="runs">对话 Run（{runsQuery.data?.total ?? 0}）</TabsTrigger>
          <TabsTrigger value="logs">程序日志（{logsQuery.data?.total ?? 0}）</TabsTrigger>
        </TabsList>
        <TabsContent value="runs" className="space-y-3">
          {runsQuery.isError && (
            <div className="rounded border border-red-500/50 p-3 text-sm text-red-500">
              加载失败：{runsQuery.error.message}
            </div>
          )}
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run / Trace</TableHead>
                  <TableHead>会话</TableHead>
                  <TableHead>用户输入</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>父 Run</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(runsQuery.data?.items ?? []).map((run) => (
                  <TableRow
                    key={run.id}
                    className="cursor-pointer"
                    data-testid={`diagnostic-run-${run.id}`}
                    onClick={() => navigate(`/diagnostics/${encodeURIComponent(run.id)}`)}
                  >
                    <TableCell className="font-mono text-xs">{short(run.id)}</TableCell>
                    <TableCell className="font-mono text-xs" title={run.session_id ?? ''}>
                      {short(run.session_id)}
                    </TableCell>
                    <TableCell className="max-w-80 truncate">
                      {String((run.input as { user_input?: string } | null)?.user_input ?? '—')}
                    </TableCell>
                    <TableCell>
                      <RunStatusBadge status={run.status} />
                    </TableCell>
                    <TableCell className="font-mono text-xs" title={run.parent_run_id ?? ''}>
                      {short(run.parent_run_id)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(run.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
                {runsQuery.isSuccess && runsQuery.data.items.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className="py-8 text-center text-sm text-muted-foreground"
                    >
                      没有匹配的 Run。
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
        <TabsContent value="logs" className="space-y-3">
          {logsQuery.data && (logsQuery.data.dropped_count || logsQuery.data.write_failures) ? (
            <p className="text-xs text-amber-500">
              已丢弃 {logsQuery.data.dropped_count ?? 0} 条日志；写库/清理失败{' '}
              {logsQuery.data.write_failures ?? 0}{' '}
              次。队列满或数据库故障时可能丢日志，请同时保留控制台或文件日志。
            </p>
          ) : null}
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>时间</TableHead>
                  <TableHead>级别</TableHead>
                  <TableHead>Logger</TableHead>
                  <TableHead>消息</TableHead>
                  <TableHead>异常</TableHead>
                  <TableHead>关联</TableHead>
                  <TableHead>代码位置</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(logsQuery.data?.items ?? []).map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatDateTime(log.created_at)}
                    </TableCell>
                    <TableCell className={`font-mono text-xs ${LEVEL_CLASS[log.level]}`}>
                      {log.level}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{log.logger}</TableCell>
                    <TableCell className="max-w-96 whitespace-pre-wrap break-words">
                      {log.message || '—'}
                    </TableCell>
                    <TableCell className="max-w-96 whitespace-pre-wrap break-words text-[11px] text-red-500">
                      {log.exception ?? '—'}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {short(log.session_id ?? log.run_id ?? log.trace_id ?? log.request_id)}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {log.pathname ? `${log.pathname}:${log.lineno}` : '—'}
                    </TableCell>
                  </TableRow>
                ))}
                {logsQuery.isSuccess && logsQuery.data.items.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      className="py-8 text-center text-sm text-muted-foreground"
                    >
                      没有匹配日志。
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!activeHasPrevious}
          onClick={() =>
            activeTab === 'runs'
              ? setRunPage((prev) => Math.max(0, prev - 1))
              : setLogPage((prev) => Math.max(0, prev - 1))
          }
        >
          上一页
        </Button>
        <span className="text-xs text-muted-foreground">第 {activePage + 1} 页</span>
        <Button
          variant="outline"
          size="sm"
          disabled={!activeHasNext}
          onClick={() =>
            activeTab === 'runs' ? setRunPage((prev) => prev + 1) : setLogPage((prev) => prev + 1)
          }
        >
          下一页
        </Button>
      </div>
    </div>
  );
}
