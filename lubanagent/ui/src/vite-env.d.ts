/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 真后端地址（dev 代理目标 / 直连 baseURL），缺省 http://localhost:8000 */
  readonly VITE_API_BASE?: string;
  /** "true" 启用 MSW mock；dev 下缺省启用，显式 "false" 关闭 */
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
