import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/contracts': 'http://localhost:8000',
      '/verify': 'http://localhost:8000',
      '/approve': 'http://localhost:8000',
      '/reject': 'http://localhost:8000',
      '/reverify': 'http://localhost:8000',
      '/fixes': 'http://localhost:8000',
      '/trust-score': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
    // Pipeline can take 30-120 s — increase proxy timeout to 3 min
    hmr: { timeout: 5000 },
  },
});
