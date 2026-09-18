import { defineConfig } from 'vite'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const appDir = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  envDir: path.resolve(appDir, '../..'),
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
