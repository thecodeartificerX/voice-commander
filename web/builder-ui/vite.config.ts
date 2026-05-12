import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/static/builder/',
  build: {
    outDir: '../../src/voice_commander/web/static/builder',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('reactflow')) return 'react-flow';
            if (id.includes('@radix-ui')) return 'radix';
            if (id.includes('zustand')) return 'zustand';
            if (id.includes('lucide-react')) return 'lucide';
            if (id.includes('react-dom') || /[\\/]node_modules[\\/]react[\\/]/.test(id) || /[\\/]node_modules[\\/]scheduler[\\/]/.test(id)) return 'react';
            return 'vendor';
          }
        },
      },
    },
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
