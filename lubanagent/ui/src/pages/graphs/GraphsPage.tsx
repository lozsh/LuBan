/**
 * /graphs 流程图：已注册确定性流程图的只读拓扑视图（列表 + 分层渲染）。
 * 数据来自 GET /v1/graphs（结构视图，实时读 DSL 不触发编译）；
 * 编辑走代码（app/graphs/），本页不做设计器。渲染确定性：BFS 分层布局，
 * 无随机/动画；条件边虚线双出边标注 then/otherwise。
 */

import { useMemo, useState } from 'react';

import { useGraphDetail, useGraphs, type GraphDetailOut } from '@/api/hooks';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';

const KIND_STYLE: Record<string, string> = {
  step: 'bg-blue-100 text-blue-700 border-blue-300 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-800',
  agent:
    'bg-purple-100 text-purple-700 border-purple-300 dark:bg-purple-950 dark:text-purple-300 dark:border-purple-800',
  human:
    'bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-800',
};

const NODE_W = 168;
const NODE_H = 64;
const LAYER_GAP = 120;
const NODE_GAP = 24;

interface Position {
  x: number;
  y: number;
}

/** BFS 从 entry 分层（确定性：按 nodes 声明序遍历同层，无排序随机性） */
function layout(detail: GraphDetailOut): Map<string, Position> {
  const adjacency = new Map<string, string[]>();
  for (const edge of detail.edges) {
    adjacency.set(edge.from_node, [...(adjacency.get(edge.from_node) ?? []), edge.to_node]);
  }
  for (const edge of detail.cond_edges) {
    adjacency.set(edge.from_node, [
      ...(adjacency.get(edge.from_node) ?? []),
      edge.then_node,
      edge.otherwise_node,
    ]);
  }
  const depth = new Map<string, number>();
  const queue: string[] = [];
  if (detail.entry) {
    depth.set(detail.entry, 0);
    queue.push(detail.entry);
  }
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const next of adjacency.get(current) ?? []) {
      if (!depth.has(next)) {
        depth.set(next, depth.get(current)! + 1);
        queue.push(next);
      }
    }
  }
  // 不可达节点（理论上 build() 已拦截孤立点）放最后一层之后，保底可见
  for (const node of detail.nodes) {
    if (!depth.has(node.name)) {
      depth.set(node.name, detail.nodes.length);
    }
  }
  const layers = new Map<number, string[]>();
  for (const node of detail.nodes) {
    const d = depth.get(node.name) ?? 0;
    layers.set(d, [...(layers.get(d) ?? []), node.name]);
  }
  const positions = new Map<string, Position>();
  for (const [d, names] of layers) {
    const maxRows = Math.max(...[...layers.values()].map((v) => v.length));
    const span = maxRows * (NODE_H + NODE_GAP);
    names.forEach((name, i) => {
      positions.set(name, {
        x: d * (NODE_W + LAYER_GAP),
        y: (span - names.length * (NODE_H + NODE_GAP)) / 2 + i * (NODE_H + NODE_GAP),
      });
    });
  }
  return positions;
}

function nodeLabel(node: {
  kind: string;
  agent_key?: string | null;
  prompt?: string | null;
  summary?: string | null;
}) {
  // 优先级：step 的 docstring 摘要 > agent 绑定 > human 审批提示 > 类型兜底
  if (node.summary) return node.summary;
  if (node.kind === 'agent' && node.agent_key) return `Agent: ${node.agent_key}`;
  if (node.kind === 'human' && node.prompt) return node.prompt;
  return node.kind === 'step' ? 'Step' : node.kind === 'human' ? '人工' : 'Agent';
}

function Topology({ detail }: { detail: GraphDetailOut }) {
  const positions = useMemo(() => layout(detail), [detail]);
  // 空节点图（0 节点）时 positions 为空，Math.max(...[]) = -Infinity；兜底最小画布
  const CANVAS_PAD = 24; // 画布四周内边距，节点不贴边
  const width = Math.max(0, ...[...positions.values()].map((p) => p.x)) + NODE_W + CANVAS_PAD * 2;
  const height = Math.max(0, ...[...positions.values()].map((p) => p.y)) + NODE_H + CANVAS_PAD * 2;
  // 坐标整体平移一个 padding：节点距画布四周各留 CANVAS_PAD
  const padded = useMemo(
    () =>
      new Map([...positions].map(([k, p]) => [k, { x: p.x + CANVAS_PAD, y: p.y + CANVAS_PAD }])),
    [positions],
  );
  const outgoing = new Set<string>([
    ...detail.edges.map((e) => e.from_node),
    ...detail.cond_edges.map((e) => e.from_node),
  ]);

  const edgePath = (from: Position, to: Position) => {
    const x1 = from.x + NODE_W;
    const y1 = from.y + NODE_H / 2;
    const x2 = to.x;
    const y2 = to.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
  };

  return (
    <div className="overflow-auto">
      <div className="relative" style={{ width, height }}>
        <svg className="absolute inset-0" width={width} height={height}>
          {detail.edges.map((edge) => {
            const from = padded.get(edge.from_node);
            const to = padded.get(edge.to_node);
            if (!from || !to) return null;
            return (
              <path
                key={`${edge.from_node}->${edge.to_node}`}
                d={edgePath(from, to)}
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                className="text-muted-foreground"
                markerEnd="url(#arrow)"
              />
            );
          })}
          {detail.cond_edges.map((edge) => {
            const from = padded.get(edge.from_node);
            const thenTo = padded.get(edge.then_node);
            const elseTo = padded.get(edge.otherwise_node);
            if (!from) return null;
            return (
              <g key={`cond:${edge.from_node}`}>
                {thenTo && (
                  <path
                    d={edgePath(from, thenTo)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    strokeDasharray="6 4"
                    className="text-muted-foreground"
                    markerEnd="url(#arrow)"
                  />
                )}
                {elseTo && (
                  <path
                    d={edgePath(from, elseTo)}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    strokeDasharray="6 4"
                    className="text-muted-foreground"
                    markerEnd="url(#arrow)"
                  />
                )}
              </g>
            );
          })}
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX={9}
              refY={5}
              markerWidth={6}
              markerHeight={6}
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
            </marker>
          </defs>
        </svg>
        {detail.nodes.map((node) => {
          const pos = padded.get(node.name)!;
          return (
            <div
              key={node.name}
              className={cn(
                'absolute rounded-lg border-2 bg-card p-2 shadow-sm',
                KIND_STYLE[node.kind] ?? '',
              )}
              style={{ left: pos.x, top: pos.y, width: NODE_W, minHeight: NODE_H }}
            >
              <div className="truncate text-sm font-medium" title={node.name}>
                {node.name}
              </div>
              <div className="truncate text-xs text-muted-foreground" title={nodeLabel(node)}>
                {nodeLabel(node)}
              </div>
              {node.name === detail.entry && (
                <Badge variant="outline" className="mt-1 text-[10px]">
                  START
                </Badge>
              )}
              {!outgoing.has(node.name) && (
                <Badge variant="outline" className="mt-1 text-[10px]">
                  END
                </Badge>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function GraphsPage() {
  const graphsQuery = useGraphs();
  const [selected, setSelected] = useState<string | null>(null);
  const detailQuery = useGraphDetail(selected);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">流程图</h1>
        <p className="text-sm text-muted-foreground">
          已注册的确定性流程图只读拓扑（编辑走代码，本页不做设计器）
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">图列表</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {graphsQuery.isLoading && <p className="text-sm text-muted-foreground">加载中…</p>}
            {graphsQuery.isError && (
              <p className="text-sm text-destructive">加载失败: {graphsQuery.error.message}</p>
            )}
            {graphsQuery.data?.map((graph) => (
              <button
                key={graph.name}
                type="button"
                onClick={() => setSelected(graph.name)}
                className={cn(
                  'w-full rounded-md border p-2 text-left text-sm hover:bg-accent',
                  selected === graph.name && 'border-primary bg-accent',
                )}
              >
                <div className="font-medium">{graph.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {graph.description || '—'}
                </div>
                {!graph.structure_available && (
                  <Badge variant="outline" className="mt-1 text-[10px]">
                    非 DSL 构建，无结构视图
                  </Badge>
                )}
              </button>
            ))}
            {graphsQuery.data?.length === 0 && (
              <p className="text-sm text-muted-foreground">
                未注册任何图——用 @graph 装饰器注册你的流程（见 app/graphs/）
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{selected ?? '选择左侧图查看拓扑'}</CardTitle>
          </CardHeader>
          <CardContent>
            {!selected && (
              <p className="text-sm text-muted-foreground">
                从左侧选择一个流程图，查看节点与分支拓扑。
              </p>
            )}
            {selected && detailQuery.isLoading && (
              <p className="text-sm text-muted-foreground">加载中…</p>
            )}
            {selected && detailQuery.isError && (
              <p className="text-sm text-destructive">加载失败: {detailQuery.error.message}</p>
            )}
            {detailQuery.data && <Topology detail={detailQuery.data} />}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
