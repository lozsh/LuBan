/**
 * JSON Schema 表单渲染器（tools 页私有）：args_schema.properties → 输入控件。
 * string → Input；number/integer → 数字 Input；boolean → Switch；enum → Select；
 * object/array/未知 → JSON Textarea。required 标星。buildArgs 负责提交前类型归一。
 */

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';

export interface SchemaProperty {
  type?: string;
  description?: string;
  enum?: unknown[];
  default?: unknown;
}

export type SchemaValues = Record<string, string>;

function propertiesOf(schema: Record<string, unknown> | undefined): Record<string, SchemaProperty> {
  return (schema?.properties ?? {}) as Record<string, SchemaProperty>;
}

function requiredOf(schema: Record<string, unknown> | undefined): string[] {
  return Array.isArray(schema?.required) ? (schema.required as string[]) : [];
}

/** 选中工具时按 schema 初始化原始表单值（boolean 默认 false，其余空串） */
export function initialValues(schema: Record<string, unknown> | undefined): SchemaValues {
  const values: SchemaValues = {};
  for (const [name, prop] of Object.entries(propertiesOf(schema))) {
    if (prop.type === 'boolean') values[name] = String(prop.default ?? false);
    else if (prop.default !== undefined) values[name] = JSON.stringify(prop.default);
    else values[name] = '';
  }
  return values;
}

/** 原始字符串值 → 执行参数；required 缺失或 JSON/数字非法时返回 error（UX 层校验，真校验以后端为准） */
export function buildArgs(
  schema: Record<string, unknown> | undefined,
  values: SchemaValues,
): { args?: Record<string, unknown>; error?: string } {
  const props = propertiesOf(schema);
  const missing = requiredOf(schema).filter((name) => (values[name] ?? '') === '');
  if (missing.length > 0) return { error: `缺少必填参数: ${missing.join(', ')}` };

  const args: Record<string, unknown> = {};
  for (const [name, prop] of Object.entries(props)) {
    const raw = values[name];
    if (raw === undefined || raw === '') continue;
    switch (prop.type) {
      case 'integer': {
        const parsed = Number.parseInt(raw, 10);
        if (Number.isNaN(parsed)) return { error: `参数 ${name} 需为整数` };
        args[name] = parsed;
        break;
      }
      case 'number': {
        const parsed = Number.parseFloat(raw);
        if (Number.isNaN(parsed)) return { error: `参数 ${name} 需为数字` };
        args[name] = parsed;
        break;
      }
      case 'boolean':
        args[name] = raw === 'true';
        break;
      case 'object':
      case 'array':
        try {
          args[name] = JSON.parse(raw);
        } catch {
          return { error: `参数 ${name} 需为合法 JSON` };
        }
        break;
      default:
        args[name] = raw;
    }
  }
  return { args };
}

export function SchemaForm({
  schema,
  values,
  onChange,
}: {
  schema: Record<string, unknown> | undefined;
  values: SchemaValues;
  onChange: (name: string, raw: string) => void;
}) {
  const props = propertiesOf(schema);
  const required = requiredOf(schema);
  const names = Object.keys(props);
  if (names.length === 0) {
    return <p className="text-sm text-muted-foreground">该工具无参数。</p>;
  }

  return (
    <div className="space-y-3">
      {names.map((name) => {
        const prop = props[name];
        const fieldId = `arg-${name}`;
        const star = required.includes(name) ? (
          <span className="ml-0.5 text-destructive">*</span>
        ) : null;
        const label = (
          <Label htmlFor={fieldId} className="font-mono text-xs">
            {name}
            {star}
            {prop.type ? <span className="ml-1 text-muted-foreground">({prop.type})</span> : null}
          </Label>
        );
        const help = prop.description ? (
          <p className="text-xs text-muted-foreground">{prop.description}</p>
        ) : null;

        if (prop.type === 'boolean') {
          return (
            <div key={name} className="flex items-center gap-2">
              <Switch
                id={fieldId}
                checked={values[name] === 'true'}
                onCheckedChange={(checked) => onChange(name, String(checked))}
              />
              {label}
              {help}
            </div>
          );
        }

        if (Array.isArray(prop.enum) && prop.enum.length > 0) {
          return (
            <div key={name} className="space-y-1.5">
              {label}
              <Select value={values[name] ?? ''} onValueChange={(value) => onChange(name, value)}>
                <SelectTrigger id={fieldId} aria-label={name} className="font-mono text-xs">
                  <SelectValue placeholder="选择取值" />
                </SelectTrigger>
                <SelectContent>
                  {prop.enum.map((option) => (
                    <SelectItem
                      key={String(option)}
                      value={String(option)}
                      className="font-mono text-xs"
                    >
                      {String(option)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {help}
            </div>
          );
        }

        if (prop.type === 'object' || prop.type === 'array') {
          return (
            <div key={name} className="space-y-1.5">
              {label}
              <Textarea
                id={fieldId}
                className="font-mono text-xs"
                rows={3}
                placeholder={prop.type === 'array' ? '["..."]' : '{"key": "value"}'}
                value={values[name] ?? ''}
                onChange={(event) => onChange(name, event.target.value)}
              />
              {help}
            </div>
          );
        }

        const numeric = prop.type === 'number' || prop.type === 'integer';
        return (
          <div key={name} className="space-y-1.5">
            {label}
            <Input
              id={fieldId}
              type={numeric ? 'number' : 'text'}
              className="font-mono text-xs"
              value={values[name] ?? ''}
              onChange={(event) => onChange(name, event.target.value)}
            />
            {help}
          </div>
        );
      })}
    </div>
  );
}
