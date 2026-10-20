import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach } from 'vitest';

import { server } from '@/mocks/node';

// jsdom 未实现 scrollIntoView（流式页自动滚动依赖），统一打桩
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => undefined);

// jsdom 未实现 blob object URL（H13 头像鉴权拉取依赖）：给确定性假 URL，测试可断言前缀
if (typeof URL.createObjectURL !== 'function') {
  let counter = 0;
  URL.createObjectURL = (() => `blob:mock-${(counter += 1)}`) as typeof URL.createObjectURL;
}
if (typeof URL.revokeObjectURL !== 'function') {
  URL.revokeObjectURL = (() => undefined) as typeof URL.revokeObjectURL;
}

// 模块级 listen：setupFiles 先于被测模块导入执行，
// 保证 src/api/client.ts 创建 openapi-fetch client 时捕获的是 MSW 补丁后的 fetch
// （否则 client 模块级固化原生 fetch，全部 API 请求绕过 MSW 直连网络）。
server.listen({ onUnhandledRequest: 'error' });
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
