#!/usr/bin/env node
/**
 * Copy That Open runtime assets from node_modules into public/ so they are
 * served same-origin by our own site — never from a CDN (CSP requirement,
 * see ~/.claude/rules/ecc/web/security.md and Phase A A-06).
 *
 * Copied assets:
 *  - @thatopen/fragments worker (self-contained module worker) → drives the
 *    Fragments render worker off the main thread. Loaded as a local blob URL by
 *    useFragmentsLoader (replaces FragmentsModels.getWorker(), which fetches
 *    from unpkg).
 *  - web-ifc WASM binaries → only needed if we ever parse raw .ifc in the
 *    browser. The .frag render path (A-06/A-07) does NOT need them, but we host
 *    them locally so any future client-side IFC parsing stays CDN-free.
 *
 * Idempotent: safe to run on every predev/prebuild. Fails loudly if a source
 * asset is missing (surfaces a broken/incompatible dependency early).
 */
import { copyFile, mkdir, access } from 'node:fs/promises'
import { constants as FS } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url))
const WEB_ROOT = resolve(SCRIPT_DIR, '..')
const NODE_MODULES = resolve(WEB_ROOT, 'node_modules')
const TARGET_DIR = resolve(WEB_ROOT, 'public', 'thatopen')

/** [sourceRelativeToNodeModules, targetFileName] */
const ASSETS = [
  ['@thatopen/fragments/dist/Worker/worker.mjs', 'fragments-worker.mjs'],
  ['web-ifc/web-ifc.wasm', 'web-ifc.wasm'],
  ['web-ifc/web-ifc-mt.wasm', 'web-ifc-mt.wasm'],
]

async function fileExists(path) {
  try {
    await access(path, FS.R_OK)
    return true
  } catch {
    return false
  }
}

async function main() {
  await mkdir(TARGET_DIR, { recursive: true })

  for (const [sourceRel, targetName] of ASSETS) {
    const source = resolve(NODE_MODULES, sourceRel)
    const target = resolve(TARGET_DIR, targetName)
    if (!(await fileExists(source))) {
      process.stderr.write(
        `[copy-thatopen-assets] MISSING source: ${source}\n` +
          '  Did `npm install` run? Is the dependency version aligned?\n',
      )
      process.exit(1)
    }
    await copyFile(source, target)
    process.stdout.write(`[copy-thatopen-assets] ${sourceRel} -> public/thatopen/${targetName}\n`)
  }
}

main().catch((err) => {
  process.stderr.write(`[copy-thatopen-assets] failed: ${err?.message ?? err}\n`)
  process.exit(1)
})
