import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The built app is written into the Python package so `opendynamo serve` (and the wheel) ship it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/opendynamo/static', emptyOutDir: true },
  worker: { format: 'es' },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
});
