import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({mode}) => {
  const target = loadEnv(mode, '.', '').API_PROXY_TARGET || 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0', port: 5173,
      proxy: {
        '/api': target,
        '/health': target,
        '/ws': {target, ws: true},
      },
    },
  };
});
