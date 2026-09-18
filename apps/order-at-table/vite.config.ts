import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig({
  plugins: [tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  envDir: path.resolve(__dirname, '../..'),
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { outDir: 'dist' },
})
