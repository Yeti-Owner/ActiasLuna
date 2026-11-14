import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const rootDir = fileURLToPath(new URL('.', import.meta.url))

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/static/',
  // `npm run dev` talks to the Python server (server/luna_server.py, default port 8080) for live data.
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/scan.glb': 'http://127.0.0.1:8080',
    },
  },
  resolve: {
    alias: {
      '@': resolve(rootDir, './'),
      'geist/font/sans': resolve(rootDir, 'src/fonts/sans.ts'),
      'geist/font/mono': resolve(rootDir, 'src/fonts/mono.ts'),
      'geist/font/pixel/variable': resolve(rootDir, 'src/fonts/pixel-variable.ts'),
      'geist/font/pixel': resolve(rootDir, 'src/fonts/pixel.ts'),
      'geist/font/serif': resolve(rootDir, 'src/fonts/serif.ts'),
    },
  },
  build: {
    outDir: 'static',
    emptyOutDir: false,
    assetsInlineLimit: 100000000, // Inline fonts as base64 to avoid text/plain MIME issues
    sourcemap: false,
    rollupOptions: {
      input: {
        main: resolve(rootDir, 'index.html'),
        dashboard: resolve(rootDir, 'dashboard.html'),
      },
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
      },
    },
  },
})
