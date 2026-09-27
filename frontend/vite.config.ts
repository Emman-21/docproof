import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/contracts': { target: 'http://localhost:8000', changeOrigin: true },
      '/verify':    { target: 'http://localhost:8000', changeOrigin: true },
      '/approve':   { target: 'http://localhost:8000', changeOrigin: true },
      '/reject':    { target: 'http://localhost:8000', changeOrigin: true },
      '/reverify':  { target: 'http://localhost:8000', changeOrigin: true },
      '/fixes':     { target: 'http://localhost:8000', changeOrigin: true },
      '/trust-score': { target: 'http://localhost:8000', changeOrigin: true },
      '/health':    { target: 'http://localhost:8000', changeOrigin: true },
    },
    // POST /verify now returns immediately (async pipeline); keep HMR snappy
    hmr: { timeout: 5000 },
  },
});
