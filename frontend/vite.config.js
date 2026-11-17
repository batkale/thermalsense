import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const page = (name) => fileURLToPath(new URL(name, import.meta.url));

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two pages, one build: the map app at / and the landing page at /about.
      // Both land in dist/ beside each other; FastAPI serves about.html for
      // /about (see _static_file in backend/main.py).
      input: {
        main:  page('./index.html'),
        about: page('./about.html'),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev backend port — must match the default in src/config.js and .env.example
      '/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') },
    },
  },
});
