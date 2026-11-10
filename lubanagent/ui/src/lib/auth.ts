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

/** 登录会话返回的最小用户视图；UI 只信任服务端返回的权限集合。 */
export interface SessionUser {
  subject: string;
  /** SessionUserOut 继承 UserPublic，额外带 id/role_keys；授权判定不使用这些字段。 */
  id?: string;
  username: string;
  display_name: string;
  role_keys?: string[];
  roles: string[];
  permissions: string[];
  auth_enabled: boolean;
  is_bootstrap: boolean;
}

export interface AuthState {
  /** 当前内存中的 Key（null = 未提供） */
  apiKey: string | null;
  /** 账号登录令牌（仅内存；刷新即失效） */
  sessionToken: string | null;
  /** 当前账号视图（null = 未登录） */
  currentUser: SessionUser | null;
  /** 最近一次收到 401（Key 缺失/失效，或网关未放行） */
  rejected: boolean;
  /**
   * 身份纪元：每次凭据/身份变化（含 401 边沿）自增。
   * 应用外壳用它作为 React key 强制整树重挂载——queryClient.clear() 会把
   * 已挂载 observer 的 query 从缓存移除导致其“搁浅”（永不重取），
   * 重挂载是让所有 useQuery 观察者以新凭据重新订阅的唯一可靠手段。
   */
  identityEpoch: number;
}

let state: AuthState = {
  apiKey: null,
  sessionToken: null,
  currentUser: null,
  rejected: false,
  identityEpoch: 0,
};

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

/** 设置 Key（内存替换，不落任何存储）；API Key 身份替换账号身份，避免旧登录残留。 */
export function setApiKey(key: string): void {
  state = {
    apiKey: key === '' ? null : key,
    sessionToken: null,
    currentUser: null,
    rejected: false,
    identityEpoch: state.identityEpoch + 1,
  };
  emit();
}

/** 设置账号登录态；账号登录替换 API Key，保证一次只有一个可信身份。 */
export function setSession(token: string, user: SessionUser): void {
  state = {
    apiKey: null,
    sessionToken: token,
    currentUser: user,
    rejected: false,
    identityEpoch: state.identityEpoch + 1,
  };
  emit();
}

/** 只清除账号登录态（保留显式直连 Key 的调试场景）。 */
export function clearSession(): void {
  state = {
    ...state,
    sessionToken: null,
    currentUser: null,
    rejected: false,
    identityEpoch: state.identityEpoch + 1,
  };
  emit();
}

/** 清除内存 Key；通知所有订阅者，顶栏徽章与依赖鉴权状态的组件立即同步。 */
export function clearApiKey(): void {
  state = { ...state, apiKey: null, rejected: false, identityEpoch: state.identityEpoch + 1 };
  emit();
}

/** 清空所有凭据（顶栏“退出登录”语义；不触碰任何持久化存储）。 */
export function clearAllCredentials(): void {
  state = {
    apiKey: null,
    sessionToken: null,
    currentUser: null,
    rejected: false,
    identityEpoch: state.identityEpoch + 1,
  };
  emit();
}

/** 401 统一入口：不自动重试，交由 UI 提示用户更换 Key / 检查网关 */
export function markUnauthorized(): void {
  if (!state.rejected) {
    // 401 边沿同时推进纪元：配合 queryClient.clear() 让搁浅的 observer 通过
    // 整树重挂载重新订阅，而不是永远停在“加载中”。
    state = { ...state, rejected: true, identityEpoch: state.identityEpoch + 1 };
    emit();
  }
}

/** 有 Key 时返回 Authorization 头；无 Key 返回空对象（后端 401 → markUnauthorized） */
export function authHeaders(): Record<string, string> {
  if (state.sessionToken !== null) {
    return { Authorization: `Bearer ${state.sessionToken}` };
  }
  if (state.apiKey === null) return {};
  return { Authorization: `Bearer ${state.apiKey}` };
}

/** 401 状态码统一识别（REST 中间件 / SSE / 裸 fetch 共用判定） */
export function isUnauthorizedStatus(status: number | null | undefined): boolean {
  return status === 401;
}
