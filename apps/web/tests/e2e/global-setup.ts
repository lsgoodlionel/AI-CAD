import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

export default async function globalSetup() {
  if (process.env.E2E_SKIP_SEED === '1') return

  const repoRoot = path.resolve(__dirname, '../../../..')
  const apiDir = path.join(repoRoot, 'apps/api')
  const script = path.join(apiDir, 'scripts/seed_e2e.py')
  const venvPython = path.join(apiDir, '.venv/bin/python')
  const python = existsSync(venvPython) ? venvPython : 'python3'

  execFileSync(python, [script], {
    cwd: apiDir,
    env: {
      ...process.env,
      DATABASE_URL: process.env.E2E_DATABASE_URL
        || process.env.DATABASE_URL
        || 'postgresql://cad_user:cad_pass@127.0.0.1:5432/cad_db',
    },
    stdio: 'inherit',
  })
}
