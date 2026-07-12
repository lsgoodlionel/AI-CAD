// 把 docs/ 手册复制到 public/manual/,供帮助中心页面同源 fetch。构建前自动同步。
import { existsSync, mkdirSync, copyFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const docs = join(webRoot, '..', '..', 'docs')
const dest = join(webRoot, 'public', 'manual')
if (!existsSync(dest)) mkdirSync(dest, { recursive: true })
for (const [src, out] of [['MODEL_MANUAL_USER.md', 'user.md'], ['MODEL_MANUAL_ADMIN.md', 'admin.md']]) {
  const s = join(docs, src)
  if (existsSync(s)) copyFileSync(s, join(dest, out))
}
console.log('[copy-manuals] done')
