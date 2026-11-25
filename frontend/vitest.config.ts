import path from 'node:path'
import { defineConfig } from 'vitest/config'

// Unit tests cover the pure helpers in src/lib; no React or Tailwind plugins needed.
export default defineConfig({
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
