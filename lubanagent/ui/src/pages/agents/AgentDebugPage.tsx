/**
 * 运行调试台（/agents/:key/debug）：围绕单个 Agent 的调试工作台，两个 Tab——
 * 「对话」多轮会话形态（每轮一个区块：用户输入 + SSE 流渲染；可取消；归档保留）
 * +「Prompt 试跑」A/B 对比（改 instructions/variables 即时试，不入库）。
 * Tab 面板 forceMount + data-[state=inactive]:hidden：切换不丢状态、后台 SSE 不断流。
 */

import { ArrowLeft, Send, Square } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';

import { API_BASE } from '@/api/client';
import { useAgent, useCancelRun } from '@/api/hooks';
import { reduceRunEvents } from '@/components/run-events';
import { RunStreamView } from '@/components/run-stream-view';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useRunStream, type SseEvent } from '@/lib/sse';

import { PromptPlaygroundPanel } from './components/prompt-playground-panel';

interface ArchivedTurn {
  input: string;
  events: SseEvent[];
}

function TurnBlock({ input, children }: { input: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex items-start gap-2">
        <Badge variant="secondary" className="mt-0.5 shrink-0">
          你
        </Badge>
        <div className="whitespace-pre-wrap rounded-md bg-muted px-3 py-2 text-sm">{input}</div>
      </div>
      <div className="pl-1">{children}</div>
    </section>
  );
}

export default function AgentDebugPage() {
  const { key = '' } = useParams();
  const agentQuery = useAgent(key);
  const stream = useRunStream();
  const cancelRun = useCancelRun();

  const [input, setInput] = useState('');
  const [turns, setTurns] = useState<ArchivedTurn[]>([]);
  const [currentInput, setCurrentInput] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 挂载生成一次、多轮共用（刷新重开新会话）——Trace 列表按会话 ID 归组调试记录
  const [sessionId] = useState(() => crypto.randomUUID());

  const reduced = useMemo(() => reduceRunEvents(stream.events), [stream.events]);
  const streaming = stream.status === 'streaming';

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [stream.events.length, turns.length]);

  const send = () => {
    const text = input.trim();
    if (text === '' || streaming) return;
    // 归档上一轮（多轮形态：历史本轮内保留）
    const archived: ArchivedTurn[] =
      currentInput !== null ? [...turns, { input: currentInput, events: stream.events }] : turns;
    if (currentInput !== null) setTurns(archived);
    // OpenAI 风格 history（v1 后端不查库，由调用方携带）
    const history = archived.flatMap((turn) => {
      const r = reduceRunEvents(turn.events);
      return [
        { role: 'user', content: turn.input },
        { role: 'assistant', content: r.output ?? r.text },
      ];
    });
    setCurrentInput(text);
    setInput('');
    void stream.start(`${API_BASE}/v1/agents/${encodeURIComponent(key)}/runs`, {
      input: text,
      session_id: sessionId,
      history,
    });
  };

  const cancel = () => {
    if (reduced.runId !== null) cancelRun.mutate(reduced.runId);
    stream.cancel();
  };

  const agent = agentQuery.data;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/agents/${encodeURIComponent(key)}`}>
            <ArrowLeft className="mr-1 h-4 w-4" />
            定义
          </Link>
        </Button>
        <h1 className="text-lg font-semibold">运行调试台</h1>
        <span className="font-mono text-sm text-muted-foreground">{key}</span>
        {agent && (
          <Badge variant="outline" className="font-mono text-xs">
            v{agent.version} · {agent.brain?.profile ?? 'smart'}
          </Badge>
        )}
      </header>

      <Tabs defaultValue="chat" className="flex min-h-0 flex-1 flex-col">
        <div className="border-b px-4 pt-2">
          <TabsList>
            <TabsTrigger value="chat">对话</TabsTrigger>
            <TabsTrigger value="playground">Prompt 试跑</TabsTrigger>
          </TabsList>
        </div>

        {/* forceMount + inactive:hidden：切换不卸载，对话轮次与试跑编辑态保留 */}
        <TabsContent
          value="chat"
          forceMount
          className="mt-0 flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
        >
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto max-w-3xl space-y-6">
          {turns.length === 0 && currentInput === null && (
            <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
              {agent?.greeting || '输入一条消息，开始调试这个 Agent。'}
            </div>
          )}

          {turns.map((turn, index) => (
            <TurnBlock key={index} input={turn.input}>
              <RunStreamView events={turn.events} status="done" error={null} />
            </TurnBlock>
          ))}

          {currentInput !== null && (
            <TurnBlock input={currentInput}>
              <RunStreamView events={stream.events} status={stream.status} error={stream.error} />
            </TurnBlock>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <Textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send();
              }
            }}
            placeholder={streaming ? '运行进行中…' : '输入消息，Enter 发送（Shift+Enter 换行）'}
            rows={2}
            disabled={streaming}
            aria-label="调试输入"
          />
          {streaming ? (
            <Button variant="destructive" onClick={cancel} disabled={cancelRun.isPending}>
              <Square className="mr-1 h-4 w-4" />
              取消
            </Button>
          ) : (
            <Button onClick={send} disabled={input.trim() === ''}>
              <Send className="mr-1 h-4 w-4" />
              发送
            </Button>
          )}
        </div>
      </div>
        </TabsContent>

        <TabsContent
          value="playground"
          forceMount
          className="mt-0 min-h-0 flex-1 overflow-y-auto data-[state=inactive]:hidden"
        >
          {agent ? (
            <PromptPlaygroundPanel agent={agent} />
          ) : (
            <div className="mx-auto max-w-3xl p-8 text-center text-sm text-muted-foreground">
              {agentQuery.isError ? 'Agent 定义加载失败，刷新重试。' : 'Agent 定义加载中…'}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
