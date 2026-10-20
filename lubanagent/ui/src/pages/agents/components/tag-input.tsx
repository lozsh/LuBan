import { X } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface TagInputProps {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  'aria-label'?: string;
}

/** 标签式字符串数组编辑：回车/添加按钮追加，点 X 移除（去重、去空白） */
export function TagInput({ values, onChange, placeholder, disabled, ...rest }: TagInputProps) {
  const [text, setText] = useState('');

  const add = () => {
    const value = text.trim();
    if (value === '' || values.includes(value)) {
      setText('');
      return;
    }
    onChange([...values, value]);
    setText('');
  };

  return (
    <div className="space-y-2">
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {values.map((value) => (
            <span
              key={value}
              className="inline-flex items-center gap-1 rounded-md border bg-secondary/50 px-2 py-0.5 font-mono text-xs"
            >
              {value}
              {!disabled && (
                <button
                  type="button"
                  aria-label={`移除 ${value}`}
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() => onChange(values.filter((item) => item !== value))}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {!disabled && (
        <div className="flex gap-2">
          <Input
            value={text}
            placeholder={placeholder}
            aria-label={rest['aria-label']}
            className="h-8 font-mono text-xs"
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                add();
              }
            }}
          />
          <Button type="button" variant="outline" size="sm" onClick={add}>
            添加
          </Button>
        </div>
      )}
    </div>
  );
}
