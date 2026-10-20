/** H13 鉴权凭据存储：仅内存、可清除、401 登记、不落任何持久化存储。 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  authHeaders,
  authStore,
  clearApiKey,
  isUnauthorizedStatus,
  markUnauthorized,
  setApiKey,
} from './auth';

afterEach(() => {
  clearApiKey();
});

describe('auth store', () => {
  it('默认无 Key：authHeaders 为空对象', () => {
    expect(authStore.getSnapshot().apiKey).toBeNull();
    expect(authHeaders()).toEqual({});
  });

  it('setApiKey 后 authHeaders 携带 Bearer；空串等同未设置', () => {
    setApiKey('sk-test-123');
    expect(authHeaders()).toEqual({ Authorization: 'Bearer sk-test-123' });
    setApiKey('');
    expect(authStore.getSnapshot().apiKey).toBeNull();
    expect(authHeaders()).toEqual({});
  });

  it('set/clear 触发订阅者；换 Key 清除 rejected 标记', () => {
    const listener = vi.fn();
    const unsubscribe = authStore.subscribe(listener);
    setApiKey('k1');
    markUnauthorized();
    expect(listener).toHaveBeenCalledTimes(2);
    expect(authStore.getSnapshot().rejected).toBe(true);
    // 换 Key 即恢复（用户提供了新凭据）
    setApiKey('k2');
    expect(authStore.getSnapshot().rejected).toBe(false);
    unsubscribe();
    setApiKey('k3');
    expect(listener).toHaveBeenCalledTimes(3); // 退订后不再通知
  });

  it('markUnauthorized 幂等：重复 401 不重复通知', () => {
    const listener = vi.fn();
    authStore.subscribe(listener);
    setApiKey('k');
    listener.mockClear();
    markUnauthorized();
    markUnauthorized();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('不写 localStorage / sessionStorage（直连 Key 不落盘的显式取舍）', () => {
    setApiKey('sk-should-not-persist');
    expect(globalThis.localStorage.length).toBe(0);
    expect(globalThis.sessionStorage.length).toBe(0);
    clearApiKey();
    expect(globalThis.localStorage.length).toBe(0);
  });

  it('isUnauthorizedStatus 只认 401', () => {
    expect(isUnauthorizedStatus(401)).toBe(true);
    expect(isUnauthorizedStatus(403)).toBe(false);
    expect(isUnauthorizedStatus(null)).toBe(false);
  });
});
