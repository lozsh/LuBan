import { Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface KeyValueEditorProps {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  disabled?: boolean;
}

type Row = [string, string];

function toRecord(rows: Row[]): Record<string, string> {
  const record: Record<string, string> = {};
  for (const [key, val] of rows) {
    if (key.trim() !== '') record[key.trim()] = val;
  }
  return record;
}

/**
 * prompt_variables 键值对编辑。
 * 行状态本地持有（避免受控重建丢焦点），父值变化且与本地不一致时回填；
 * 每次编辑把非空键提交给父层。末行恒为空白"新键"行。
 */
export function KeyValueEditor({ value, onChange, disabled }: KeyValueEditorProps) {
  const [rows, setRows] = useState<Row[]>(() => [...Object.entries(value), ['', '']]);

  useEffect(() => {
    setRows((prev) => {
      if (JSON.stringify(toRecord(prev)) === JSON.stringify(value)) return prev;
      return [...Object.entries(value), ['', '']];
    });
  }, [value]);

  const update = (next: Row[]) => {
    setRows(next);
    onChange(toRecord(next));
  };

  const setRow = (index: number, key: string, val: string) => {
    const next = rows.map((row, i) => (i === index ? ([key, val] as Row) : row));
    // 编辑末行且键非空 → 追加新的空白末行
    if (index === next.length - 1 && key.trim() !== '') next.push(['', '']);
    update(next);
  };

  const removeRow = (index: number) => {
    update(rows.filter((_, i) => i !== index));
  };

  if (disabled) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <p className="text-xs text-muted-foreground">无变量</p>;
    return (
      <div className="space-y-2">
        {entries.map(([key, val]) => (
          <div key={key} className="flex items-center gap-2">
            <Input value={key} readOnly disabled className="h-8 w-48 font-mono text-xs" />
            <Input value={val} readOnly disabled className="h-8 flex-1 font-mono text-xs" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {rows.map(([key, val], index) => (
        <div key={index} className="flex items-center gap-2">
          <Input
            value={key}
            placeholder="变量名"
            aria-label={`变量 ${index + 1} 名`}
            className="h-8 w-48 font-mono text-xs"
            onChange={(event) => setRow(index, event.target.value, val)}
          />
          <Input
            value={val}
            placeholder="默认值"
            aria-label={`变量 ${index + 1} 值`}
            className="h-8 flex-1 font-mono text-xs"
            onChange={(event) => setRow(index, key, event.target.value)}
          />
          {key.trim() !== '' && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`删除变量 ${key}`}
              onClick={() => removeRow(index)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}
