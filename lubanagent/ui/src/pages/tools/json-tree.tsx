/** 轻量 JSON 树渲染（tools 执行结果面板用，递归嵌套列表）。 */

export function JsonTree({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">null</span>;
  }
  if (typeof value === 'string') {
    return <span className="text-emerald-500">{JSON.stringify(value)}</span>;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return <span className="text-amber-500">{JSON.stringify(value)}</span>;
  }
  const entries: Array<[string, unknown]> = Array.isArray(value)
    ? value.map((item, index) => [String(index), item])
    : Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) {
    return <span className="text-muted-foreground">{Array.isArray(value) ? '[]' : '{}'}</span>;
  }
  return (
    <ul className="ml-3 space-y-0.5 border-l pl-3">
      {entries.map(([name, item]) => (
        <li key={name} className="font-mono text-xs">
          <span className="text-muted-foreground">{name}: </span>
          <JsonTree value={item} />
        </li>
      ))}
    </ul>
  );
}
