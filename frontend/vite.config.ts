import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Windows: shells started by editors often use a lower-case drive letter (`c:\...`). Vite then resolves some
// modules as `c:\` and others as `C:\`, bundling React twice ("Cannot read properties of null (reading
// 'useRef')"). Pin the root to an upper-case drive letter and dedupe React.
const root = import.meta.dirname.replace(/^[a-z]:/, (d) => d.toUpperCase())

export default defineConfig({
  root,
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(root, 'src') }, dedupe: ['react', 'react-dom'] },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
