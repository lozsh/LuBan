/**
 * 轻量 JSON 树（Trace 回放页私有）：对象/数组可折叠，叶子按类型着色。
 */

import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';

function Leaf({ value }: { value: unknown }) {
  if (value === null) return <span className="text-muted-foreground">null</span>;
  switch (typeof value) {
    case 'string':
      return <span className="text-emerald-600 dark:text-emerald-400">&quot;{value}&quot;</span>;
    case 'number':
      return <span className="text-blue-600 dark:text-blue-400">{String(value)}</span>;
    case 'boolean':
      return <span className="text-orange-600 dark:text-orange-400">{String(value)}</span>;
    default:
      return <span className="text-muted-foreground">{String(value)}</span>;
  }
}

function Node({ name, value, depth }: { name: string | null; value: unknown; depth: number }) {
  const isContainer = value !== null && typeof value === 'object';
  const [open, setOpen] = useState(depth < 2);

  if (!isContainer) {
    return (
      <div className="flex gap-1 leading-5">
        {name !== null && <span className="shrink-0 text-foreground/80">{name}:</span>}
        <Leaf value={value} />
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value as Record<string, unknown>);
  const preview = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`;

  return (
    <div>
      <button
        type="button"
        className="flex items-center gap-1 leading-5 hover:text-primary"
        onClick={() => setOpen((prev) => !prev)}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {name !== null && <span className="text-foreground/80">{name}:</span>}
        <span className="text-muted-foreground">{preview}</span>
      </button>
      {open && (
        <div className="ml-4 border-l border-border/60 pl-2">
          {entries.map(([childName, childValue]) => (
            <Node key={childName} name={childName} value={childValue} depth={depth + 1} />
          ))}
          {entries.length === 0 && <div className="text-muted-foreground">（空）</div>}
        </div>
      )}
    </div>
  );
}

export function JsonTree({ value }: { value: unknown }) {
  return (
    <div className="overflow-x-auto font-mono text-xs" data-testid="json-tree">
      <Node name={null} value={value} depth={0} />
    </div>
  );
}
