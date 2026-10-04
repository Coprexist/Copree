/**
 * i18n key 闸门：把每个 t() 调用点拿去**真字典**里解析一遍。
 *
 * 为什么需要它：`check-i18n.mjs` 只统计静态字面量，看不见动态 key
 * （`t(l.i18nKey)` 这种），于是 `'settings.chinese'` 这类**拆命名空间前的点号形式**
 * 能一路漏到界面上——没有冒号＝按 common 命名空间查，查不到就原样回显 key。
 *
 * 做法：用 esbuild 就地转译 ns/*.ts（不再依赖跑着的 dev server），逐个 key 解析；
 * 三语缺任何一个都算失败。点号形式（前缀是命名空间名）单独列出来，那是迁移漏网。
 *
 *   node scripts/check-i18n-keys.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import esbuild from 'esbuild'

const ROOT = path.resolve(import.meta.dirname, '..')
const SRC = path.join(ROOT, 'src')
const NS_DIR = path.join(SRC, 'i18n/ns')
const LANGS = ['zh', 'en', 'ja']
const LANG_SUFFIX = { Zh: 'zh', En: 'en', Ja: 'ja' }
/** 这些字段名后面跟的字符串是 key（散落在各处的数据表） */
const KEY_FIELDS = ['i18nKey', 'labelKey', 'keyLabel', 'descKey', 'hintKey', 'titleKey']

/** ns/*.ts → { zh: { ns: {key: 文案} }, ... }：转译后 import，避免自己解析 TS */
async function loadDicts() {
  const dicts = { zh: {}, en: {}, ja: {} }
  for (const file of fs.readdirSync(NS_DIR).filter((f) => f.endsWith('.ts'))) {
    const ns = file.replace(/\.ts$/, '')
    const code = fs.readFileSync(path.join(NS_DIR, file), 'utf8')
    const { code: js } = await esbuild.transform(code, { loader: 'ts', format: 'esm' })
    const mod = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'))
    for (const [name, value] of Object.entries(mod)) {
      const suffix = name.match(/(Zh|En|Ja)$/)?.[1]
      if (suffix) dicts[LANG_SUFFIX[suffix]][ns] = value
    }
  }
  return dicts
}

function* sourceFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules') yield* sourceFiles(full)
    } else if (/\.(ts|tsx)$/.test(entry.name)) {
      yield full
    }
  }
}

/** 注释里也会出现 key 示例，不剥掉会误报 */
const isCommentLine = (line) => {
  const s = line.trim()
  return s.startsWith('//') || s.startsWith('*') || s.startsWith('/*') || s.startsWith('{/*')
}

function collectUsed(namespaces) {
  const used = new Map() // key → 首个出现的文件
  const stale = new Map() // 点号形式但前缀是命名空间名 → 迁移漏网
  for (const file of sourceFiles(SRC)) {
    const rel = path.relative(SRC, file)
    const text = fs.readFileSync(file, 'utf8').split('\n').filter((l) => !isCommentLine(l)).join('\n')
    for (const m of text.matchAll(/\bt\(\s*'([^']+)'/g)) if (!used.has(m[1])) used.set(m[1], rel)
    for (const field of KEY_FIELDS) {
      for (const m of text.matchAll(new RegExp(`${field}:\\s*'([^']+)'`, 'g'))) {
        const value = m[1]
        // 字段值必须是 ns:key；无冒号的只认两种：common 里真有这个 key，或纯格式标记（JSON/TXT/HTML 这类
        // 不可翻译的展示字面量，写 labelKey 只是个字段名习惯）——其余无冒号值＝硬编码文案
        if (value.includes(':') || value in dicts.zh.common || /^[A-Za-z0-9_+#.-]+$/.test(value)) continue
        if (!used.has(value)) used.set(value, rel)
      }
    }
    for (const m of text.matchAll(/['"]([A-Za-z][A-Za-z0-9_]*(?:[:.][A-Za-z0-9_.]+)+)['"]/g)) {
      // ns/*.ts 里带点的字符串是**键名本身**（如 opencli 的 preset.fileRead），不是调用点，跳过
      if (rel.startsWith('i18n/ns/')) break
      const key = m[1]
      const sep = key.search(/[:.]/)
      if (namespaces.has(key.slice(0, sep)) && key[sep] === '.') stale.set(key, rel)
    }
  }
  return { used, stale }
}

const split = (key) => {
  const i = key.indexOf(':')
  return i === -1 ? ['common', key] : [key.slice(0, i), key.slice(i + 1)]
}

const dicts = await loadDicts()
const namespaces = new Set(Object.keys(dicts.zh))
const { used, stale } = collectUsed(namespaces)

const missing = []
for (const [key, file] of used) {
  const [ns, k] = split(key)
  for (const lang of LANGS) {
    if (!dicts[lang][ns] || !(k in dicts[lang][ns])) missing.push(`${lang}  ${key}  @ ${file}`)
  }
}

console.log(`i18n key 闸门：${namespaces.size} 个命名空间，检查 ${used.size} 个调用点`)
if (stale.size) {
  console.log(`\n✗ 点号形式（无冒号＝按 common 查，必然取不到）${stale.size} 处：`)
  for (const [key, file] of stale) console.log(`  ${key}  @ ${file}`)
}
if (missing.length) {
  console.log(`\n✗ 解析不到 ${missing.length} 处：`)
  for (const m of missing) console.log('  ' + m)
}
if (missing.length || stale.size) process.exit(1)
console.log('✓ 全部可解析')
