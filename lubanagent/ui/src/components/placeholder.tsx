import { Hammer } from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

interface PlaceholderProps {
  title: string;
  description: string;
  /** 该页面将接入的 API，给 G1/G2/G3 的实现提示 */
  apis?: string[];
}

/** G0 占位页：路由可达、布局完整，页面能力由后续页面组填实 */
export function Placeholder({ title, description, apis = [] }: PlaceholderProps) {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <Card className="w-full max-w-lg border-dashed">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Hammer className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-xl">{title}</CardTitle>
          </div>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        {apis.length > 0 && (
          <CardContent>
            <p className="mb-2 text-xs font-medium text-muted-foreground">将接入的 API</p>
            <ul className="space-y-1">
              {apis.map((api) => (
                <li key={api} className="font-mono text-xs text-muted-foreground">
                  {api}
                </li>
              ))}
            </ul>
          </CardContent>
        )}
      </Card>
    </div>
  );
}
