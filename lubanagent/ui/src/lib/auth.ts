/**
 * 工作台鉴权凭据（直连 API Key）——仅内存保存（契约 H13）：
 *
 * - 后端 `auth_enabled=true` 时所有 /v1 路由要求 `Authorization: Bearer <key>`；
 * - Key 只存在本模块的进程内变量中：不进 VITE 构建变量、不进 URL query、
 *   **不默认持久化 localStorage/sessionStorage**（用户刷新页面需重新提供 Key，
 *   这是"直连 Key 不落盘"的显式取舍——宁可用重新输入换取不落盘）；
 * - `markUnauthorized()` 由 401 响应统一触发（REST 中间件 / SSE / 裸 fetch 共用），
 *   置 `rejected=true` 供顶栏鉴权提示展示；不做自动重试（401 需要用户换 Key，
 *   盲重试只会撞出死循环）；
 * - 可信网关部署（后端前置网关统一注入身份）时无需填 Key：rejected 提示即网关配置问题。
 */

export interface AuthState {
  /** 当前内存中的 Key（null = 未提供） */
  apiKey: string | null;
  /** 最近一次收到 401（Key 缺失/失效，或网关未放行） */
  rejected: boolean;
}

let state: AuthState = { apiKey: null, rejected: false };

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function snapshot(): AuthState {
  return state;
}

/** React useSyncExternalStore 用的固定引用 getter（state 不可变替换） */
export const authStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: snapshot,
};

/** 设置 Key（内存替换，不落任何存储）；换 Key 即清除 401 标记 */
export function setApiKey(key: string): void {
  state = { apiKey: key === '' ? null : key, rejected: false };
  emit();
}

/** 清除内存 Key（用户主动登出语义；页面刷新同样等效清零） */
export function clearApiKey(): void {
  state = { apiKey: null, rejected: false };
  emit();
}

/** 401 统一入口：不自动重试，交由 UI 提示用户更换 Key / 检查网关 */
export function markUnauthorized(): void {
  if (!state.rejected) {
    state = { ...state, rejected: true };
    emit();
  }
}

/** 有 Key 时返回 Authorization 头；无 Key 返回空对象（后端 401 → markUnauthorized） */
export function authHeaders(): Record<string, string> {
  if (state.apiKey === null) return {};
  return { Authorization: `Bearer ${state.apiKey}` };
}

/** 401 状态码统一识别（REST 中间件 / SSE / 裸 fetch 共用判定） */
export function isUnauthorizedStatus(status: number | null | undefined): boolean {
  return status === 401;
}
