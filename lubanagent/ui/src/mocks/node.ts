import { setupServer } from 'msw/node';

import { handlers } from './handlers';

/** vitest node 模式的 MSW server（src/test/setup.ts 启动） */
export const server = setupServer(...handlers);
