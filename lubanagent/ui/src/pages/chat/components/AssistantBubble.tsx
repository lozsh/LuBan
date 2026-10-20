/**
 * 用户端聊天气泡（/chat 专用）：Run SSE 事件流 → 用户视角渲染。
 * 与调试台 RunStreamView 的区别：工具调用只显示状态行（不折叠参数/结果），
 * 不渲染模型回退/预算提示/Run 汇总卡；终态后 markdown 渲染回复；
 * 审批请求给批准/拒绝按钮（第一个消费 useDecideApproval 的页面）。
 */

import { CheckCircle2, Loader2, ShieldQuestion, Wrench, XCircle } from 'lucide-react';
import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { asRecord, reduceRunEvents } from '@/components/run-events';
import { Button } from '@/components/ui/button';
import type { RunStreamStatus, SseEvent } from '@/lib/sse';

/** 终态后 markdown 渲染（流式期纯文本防半截语法抖动）；不引 rehype-raw，HTML 转义防注入 */
function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="chat-markdown text-sm leading-6" data-testid="chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 list-disc pl-5 last:mb-0">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 list-decimal pl-5 last:mb-0">{children}</ol>,
          li: ({ children }) => <li className="mb-0.5">{children}</li>,
          h3: ({ children }) => (
            <h3 className="mb-1 mt-2 text-sm font-semibold first:mt-0">{children}</h3>
          ),
          h4: ({ children }) => (
            <h4 className="mb-1 mt-2 text-sm font-semibold first:mt-0">{children}</h4>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-muted-foreground/40 pl-3 last:mb-0">
              {children}
            </blockquote>
          ),
          code: ({ children, className }) =>
            className?.includes('language-') ? (
              <code className="block overflow-x-auto rounded bg-background p-2 font-mono text-xs">
                {children}
              </code>
            ) : (
              <code className="rounded bg-background px-1 py-0.5 font-mono text-xs">
                {children}
              </code>
            ),
          pre: ({ children }) => <pre className="mb-2 last:mb-0">{children}</pre>,
          table: ({ children }) => (
            <table className="mb-2 w-full border-collapse text-xs last:mb-0">{children}</table>
          ),
          th: ({ children }) => (
            <th className="border px-2 py-1 text-left font-medium">{children}</th>
          ),
          td: ({ children }) => <td className="border px-2 py-1">{children}</td>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/** 工具调用状态行：图标 + 工具名 + 状态，不显示参数/结果/耗时（用户端干净） */
function ToolStatusRow({ tool, status }: { tool: string; status: 'running' | 'done' | 'failed' }) {
  const icon =
    status === 'running' ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500" />
    ) : status === 'done' ? (
      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
    ) : (
      <XCircle className="h-3.5 w-3.5 text-red-500" />
    );
  return (
    <div
      className="flex items-center gap-1.5 text-xs text-muted-foreground"
      data-testid="chat-tool-row"
    >
      {icon}
      <Wrench className="h-3 w-3" />
      <span className="font-mono">{tool}</span>
      <span>{status === 'running' ? '执行中…' : status === 'done' ? '完成' : '失败'}</span>
    </div>
  );
}

export interface AssistantBubbleProps {
  events: SseEvent[];
  status: RunStreamStatus;
  error: string | null;
  /** 审批决定回调（ChatPage 持 useDecideApproval） */
  onDecide: (approvalId: string, approved: boolean) => void;
  /** 审批决定进行中（按钮防抖）；-1 表示无进行中的决定 */
  decidingApprovalId: string | null;
  /** 网络/HTTP 层错误的状态码，401 时附加鉴权提示 */
  errorStatus?: number | null;
}

export function AssistantBubble({
  events,
  status,
  error,
  onDecide,
  decidingApprovalId,
  errorStatus,
}: AssistantBubbleProps) {
  const reduced = useMemo(() => reduceRunEvents(events), [events]);
  const streaming = status === 'streaming';
  // 审批请求 + 决定事件成对收进 notices；resolved 到达后按钮消失显示结果
  const approvalRequested = reduced.notices.find(
    (notice) => notice.type === 'tool_approval_requested',
  );
  const approvalResolved = reduced.notices.find(
    (notice) => notice.type === 'tool_approval_resolved',
  );

  return (
    <div className="space-y-2" data-testid="assistant-bubble">
      {reduced.text !== '' &&
        (streaming || !reduced.finished ? (
          <div className="whitespace-pre-wrap text-sm leading-6">
            {reduced.text}
            {streaming && (
              <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-primary/70 align-text-bottom" />
            )}
          </div>
        ) : (
          <MarkdownMessage text={reduced.output ?? reduced.text} />
        ))}

      {reduced.tools.map((entry) => (
        <ToolStatusRow key={entry.id} tool={entry.tool} status={entry.status} />
      ))}

      {approvalRequested && !approvalResolved && (
        <div
          className="flex items-center gap-2 rounded-md border border-orange-500/50 bg-orange-500/10 px-3 py-2 text-xs"
          data-testid="chat-approval-card"
        >
          <ShieldQuestion className="h-4 w-4 shrink-0 text-orange-500" />
          <span className="flex-1">
            该操作需要人工确认：
            <span className="font-mono">{String(asRecord(approvalRequested.data).tool ?? '')}</span>
            {/** 可信决定人边界（H13）：决定以当前页面提供的身份提交并记录 decided_by；
             * 工作台不做逐人身份核验，共用同一 Key 的操作者在后端看来是同一主体 */}
            <span className="block text-[11px] text-muted-foreground">
              决定将以当前页面身份（decided_by）提交并留痕；不另做逐人身份核验。
            </span>
          </span>
          <Button
            size="sm"
            variant="outline"
            className="h-7 border-emerald-500/60 text-emerald-600 hover:text-emerald-600 dark:text-emerald-400"
            disabled={decidingApprovalId !== null}
            onClick={() =>
              void onDecide(String(asRecord(approvalRequested.data).approval_id ?? ''), true)
            }
          >
            批准
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 border-red-500/60 text-red-600 hover:text-red-600 dark:text-red-400"
            disabled={decidingApprovalId !== null}
            onClick={() =>
              void onDecide(String(asRecord(approvalRequested.data).approval_id ?? ''), false)
            }
          >
            拒绝
          </Button>
        </div>
      )}

      {approvalResolved && (
        <div className="text-xs text-muted-foreground" data-testid="chat-approval-resolved">
          {asRecord(approvalResolved.data).approved ? '✓ 已批准，继续执行' : '已拒绝该操作'}
        </div>
      )}

      {reduced.cancelled && <div className="text-xs text-muted-foreground">已停止</div>}

      {reduced.failure && (
        <div
          className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400"
          data-testid="chat-error-card"
        >
          {reduced.failure.message}
        </div>
      )}

      {status === 'error' && error !== null && (
        <div
          className="rounded-md border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400"
          data-testid="chat-error-card"
        >
          {error}
          {errorStatus === 401 && (
            <div className="mt-1 text-xs">
              后端已开启 API Key 鉴权（AUTH_ENABLED=true）。请使用 docker-compose
              演示栈，或在业务网关注入凭证后访问。
            </div>
          )}
        </div>
      )}

      {streaming && reduced.text === '' && reduced.tools.length === 0 && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在输入…
        </div>
      )}
    </div>
  );
}
