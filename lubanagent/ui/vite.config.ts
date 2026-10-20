/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// VITE_API_BASE 切真后端；dev 下 /v1 经代理转发，避免 CORS
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiBase = env.VITE_API_BASE || 'http://localhost:8000';
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/v1': { target: apiBase, changeOrigin: true },
        '/health': { target: apiBase, changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      // 测试内 fetch 需要绝对 URL（node/undici）；MSW path-only 匹配任意 origin
      env: {
        VITE_API_BASE: 'http://localhost',
        VITE_USE_MOCK: 'false',
      },
    },
  };
});
