import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/static/builder/',
  build: {
    outDir: '../../src/voice_commander/web/static/builder',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8765', changeOrigin: true },
      '/events': { target: 'http://localhost:8765', changeOrigin: true, ws: false },
      '/page': { target: 'http://localhost:8765', changeOrigin: true },
    },
  },
})
