/**
 * /evals 评测报告：报告列表（dataset/agent/version/model/pass_rate/时间）+ 详情
 * （逐 case verdicts：case 名 / pass-fail / 四类断言（输出/工具/judge/拒答）图标 / detail 展开）。
 * 数据来自真实 eval_results（`luban eval run` 落库，R5 评测体系打通）；dev mock 模式吃 msw fixtures。
 */

import { CheckCircle2, ChevronDown, ChevronRight, MinusCircle, XCircle } from 'lucide-react';
import { Fragment, useState } from 'react';

import {
  useEvalDetail,
  useEvals,
  type EvalAssertionVerdict,
  type EvalReportSummary,
} from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatDateTime } from '@/lib/format';

/** 断言列：kind → 中文列头（与后端 AssertionVerdict.kind 一一对应） */
const ASSERTION_COLUMNS: Array<{
  kind: EvalAssertionVerdict['kind'];
  label: string;
}> = [
  { kind: 'output', label: '输出断言' },
  { kind: 'tool_call', label: '工具断言' },
  { kind: 'judge', label: 'judge 评分' },
  { kind: 'refusal', label: '拒答断言' },
];

function verdictOf(
  verdicts: EvalAssertionVerdict[] | undefined,
  kind: EvalAssertionVerdict['kind'],
): EvalAssertionVerdict | undefined {
  return verdicts?.find((verdict) => verdict.kind === kind);
}

function VerdictIcon({ verdict }: { verdict: EvalAssertionVerdict | undefined }) {
  if (!verdict) return <span className="text-muted-foreground">—</span>;
  // error verdict 中立（judge 解析失败，不计 pass/fail）
  if (verdict.error) return <MinusCircle className="h-4 w-4 text-muted-foreground" aria-label="中立" />;
  return verdict.passed ? (
    <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-label="通过" />
  ) : (
    <XCircle className="h-4 w-4 text-destructive" aria-label="未通过" />
  );
}

function passRateText(rate: number, passed: number, total: number): string {
  return `${(rate * 100).toFixed(0)}% (${passed}/${total})`;
}

function ReportDetail({ report }: { report: EvalReportSummary }) {
  const detailQuery = useEvalDetail(report.id);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(caseName: string): void {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(caseName)) next.delete(caseName);
      else next.add(caseName);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          报告详情 — <span className="font-mono">{report.dataset_key}</span>
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            {report.agent_key}@v{report.agent_version} · {report.model_key || '—'} · 通过率{' '}
            {passRateText(report.pass_rate, report.passed_cases, report.total_cases)} · 错答率{' '}
            {(report.wrong_answer_rate * 100).toFixed(0)}%
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {detailQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
        {detailQuery.error && (
          <p className="text-sm text-destructive">加载失败：{detailQuery.error.message}</p>
        )}
        {detailQuery.isSuccess && detailQuery.data.results.length === 0 && (
          <p className="text-sm text-muted-foreground">该报告暂无逐 case 结果。</p>
        )}
        {detailQuery.data && detailQuery.data.results.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Case</TableHead>
                <TableHead>结果</TableHead>
                {ASSERTION_COLUMNS.map((column) => (
                  <TableHead key={column.kind}>{column.label}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {detailQuery.data.results.map((result) => {
                const isOpen = expanded.has(result.case);
                return (
                  <Fragment key={result.case}>
                    <TableRow>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6"
                          aria-label={`展开 ${result.case}`}
                          onClick={() => toggle(result.case)}
                        >
                          {isOpen ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </Button>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{result.case}</TableCell>
                      <TableCell>
                        <Badge variant={result.passed ? 'default' : 'destructive'}>
                          {result.passed ? '通过' : '未通过'}
                        </Badge>
                      </TableCell>
                      {ASSERTION_COLUMNS.map((column) => (
                        <TableCell key={column.kind}>
                          <VerdictIcon verdict={verdictOf(result.verdicts, column.kind)} />
                        </TableCell>
                      ))}
                    </TableRow>
                    {isOpen && (
                      <TableRow>
                        <TableCell />
                        <TableCell colSpan={6}>
                          <ul className="space-y-1 py-1 text-xs text-muted-foreground">
                            {ASSERTION_COLUMNS.map((column) => {
                              const item = verdictOf(result.verdicts, column.kind);
                              if (!item) return null;
                              return (
                                <li key={column.kind}>
                                  <span className="font-medium text-foreground">
                                    {column.label}：
                                  </span>
                                  {item.error ? '判定出错（中立）' : item.passed ? '通过' : '未通过'}
                                  {item.detail ? ` — ${item.detail}` : ''}
                                </li>
                              );
                            })}
                            {result.run_id && <li>run_id：{result.run_id}</li>}
                            {result.output && <li>输出：{result.output}</li>}
                            {result.error && (
                              <li className="text-destructive">
                                运行错误：{String(result.error.message ?? result.error)}
                              </li>
                            )}
                          </ul>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export default function EvalsPage() {
  const evalsQuery = useEvals();
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const reports = evalsQuery.data ?? [];
  const selected = reports.find((report) => report.id === selectedId) ?? null;

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">评测报告</h1>
        <p className="text-sm text-muted-foreground">
          数据集回归报告的通过率与逐 case 断言结果（由 luban eval run 落库）。
        </p>
      </div>

      {evalsQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {evalsQuery.error && (
        <p className="text-sm text-destructive">加载失败：{evalsQuery.error.message}</p>
      )}

      {evalsQuery.isSuccess && (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>数据集</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>版本</TableHead>
                <TableHead>模型</TableHead>
                <TableHead>通过率</TableHead>
                <TableHead>错答率</TableHead>
                <TableHead>时间</TableHead>
                <TableHead className="w-20">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {reports.map((report) => (
                <TableRow key={report.id}>
                  <TableCell className="font-mono text-xs font-medium">
                    {report.dataset_key}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{report.agent_key}</TableCell>
                  <TableCell className="font-mono text-xs">v{report.agent_version}</TableCell>
                  <TableCell className="font-mono text-xs">{report.model_key || '—'}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {passRateText(report.pass_rate, report.passed_cases, report.total_cases)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {(report.wrong_answer_rate * 100).toFixed(0)}%
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatDateTime(report.created_at)}
                  </TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" onClick={() => setSelectedId(report.id)}>
                      查看
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {reports.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="text-center text-sm text-muted-foreground">
                    暂无评测报告。
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      {selected && <ReportDetail key={selected.id} report={selected} />}
    </div>
  );
}
