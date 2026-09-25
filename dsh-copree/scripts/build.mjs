// SPDX-License-Identifier: MIT
/**
 * Build both plugin halves.
 * - Host half (`lib/index.js`): ESM for the harness process; only Node builtins
 *   and framework externals are used, so the bundle stays self-contained.
 * - Client half (`lib/client.js`): CJS wrapped in the web boot factory
 *   (`window.__ModuleLoader__.load({ id, factory })`), the format the
 *   client-module system materializes at `/plugins/<id>/client.js`.
 *   `react` stays external (the shell's own instance resolves at load time).
 */
import { build } from 'esbuild'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

const ID = 'dsh-copree'
const HOST_EXTERNALS = ['@deepseek-ai/schemastery', '@deepseek-ai/dsh-settings', '@deepseek-ai/dsh-scope']
// Client externals stay external at bundle time and resolve through the web
// ModuleLoader at runtime (same mechanism the shipped ui-* bundles use).
const CLIENT_EXTERNALS = [
  'react',
  'react/jsx-runtime',
  // Reuse the shipped Markdown/KaTeX renderer so Copree messages render
  // exactly like DSH conversation text (GFM + LaTeX + safe-HTML filtering).
  '@deepseek-ai/dsh-client-ui-primitives',
]

mkdirSync(join(root, 'lib'), { recursive: true })

await build({
  entryPoints: [join(root, 'src/index.ts')],
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'es2024',
  outfile: join(root, 'lib/index.js'),
  sourcemap: true,
  external: HOST_EXTERNALS,
})

await build({
  entryPoints: [join(root, 'src/client.ts')],
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  target: 'es2022',
  outfile: join(root, 'lib/client.js'),
  sourcemap: true,
  external: CLIENT_EXTERNALS,
  define: {
    'process.env.NODE_ENV': JSON.stringify(process.env.NODE_ENV ?? 'production'),
  },
  banner: {
    js: `window.__ModuleLoader__.load({ id: ${JSON.stringify(ID)}, factory: (require) => {\nvar module = { exports: {} }; var exports = module.exports;`,
  },
  footer: {
    js: 'return module.exports; } });',
  },
})

const clientBundle = readFileSync(join(root, 'lib/client.js'), 'utf8')
// Purity: the only @deepseek-ai packages allowed in the client bundle are the
// declared runtime externals (resolved by the web ModuleLoader). Anything else
// would mean a value import got bundled in, which must not happen.
const allowed = new Set(CLIENT_EXTERNALS.filter((id) => id.startsWith('@deepseek-ai/')))
for (const match of clientBundle.matchAll(/@deepseek-ai\/[a-z0-9-]+/g)) {
  if (!allowed.has(match[0])) {
    throw new Error(
      `client bundle purity: '@deepseek-ai/${match[0].slice('@deepseek-ai/'.length)}' must not reach the client bundle `
      + '(declare it in CLIENT_EXTERNALS if it is a runtime ModuleLoader dependency)',
    )
  }
}

// ── 构建清单：自更新的内容寻址身份 ──────────────────────────────────
// 记录每个运行时产物的 sha256；对清单取摘要即为本次构建的身份，因此不需要
// 人工维护版本号。sourcemap 属调试辅助，不进清单，避免注释改动也改变身份。
// package.json 一并纳管：它的 files/exports/dsh 字段同样决定插件能否加载。
const TRACKED = ['package.json', 'lib/index.js', 'lib/client.js']

function listDist(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...listDist(abs))
    else if (!entry.name.endsWith('.map')) out.push(abs)
  }
  return out
}

const distRoot = join(root, 'dist')
const artifacts = [
  ...TRACKED.map((rel) => join(root, rel)),
  ...(existsSync(distRoot) ? listDist(distRoot) : []),
]

const files = {}
for (const abs of artifacts.sort()) {
  files[relative(root, abs).split('\\').join('/')] = createHash('sha256').update(readFileSync(abs)).digest('hex')
}

// 记录构建时后端版本，供运行时检测“插件内置 UI 与后端 API 是否已漂移”
const backendUrl = process.env.AISCHAT_BACKEND_URL ?? 'http://127.0.0.1:5228'
let backendVersion = null
try {
  const res = await fetch(new URL('/health', backendUrl), { signal: AbortSignal.timeout(1500) })
  if (res.ok) backendVersion = (await res.json()).version ?? null
} catch { /* 后端未运行时不阻塞构建，清单里留 null */ }

const manifest = {
  name: 'dsh-copree',
  version: JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version,
  buildStamp: new Date().toISOString(),
  backendVersion,
  files,
}
writeFileSync(join(root, 'lib/manifest.json'), JSON.stringify(manifest, null, 2) + '\n', 'utf8')

console.log(`built lib/index.js and lib/client.js (${Object.keys(files).length} artifacts, backend=${backendVersion ?? 'n/a'})`)
