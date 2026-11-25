// Run Vitest from a cwd whose drive letter is upper-case. On Windows, editors often start shells in `c:\...`,
// and Vitest then loads two copies of itself ("Vitest failed to find the current suite").
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url)).replace(/^[a-z]:/, (d) => d.toUpperCase())
const r = spawnSync(process.execPath, ['node_modules/vitest/vitest.mjs', 'run', ...process.argv.slice(2)],
  { cwd: root, stdio: 'inherit' })
process.exit(r.status ?? 1)
