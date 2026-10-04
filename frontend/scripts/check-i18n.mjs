#!/usr/bin/env node
/**
 * i18n 静态检查：源码里用到的 key，三语字典里是否都有定义
 * 覆盖两种来源：t('x.y') 调用点，以及常量表里的 nameKey/descKey/labelKey 字段
 *
 * 为什么需要它：getTranslation() 找不到 key 时**原样返回 key**，
 * 界面上就会直接出现 'admin.addProvider'、'adminConfig:sourceDb' 这种源码串。
 * 这类问题不报错、不崩溃，只在肉眼看界面时才发现，所以用脚本兜住。
 *
 * 用法（仓库根目录）：node frontend/scripts/check-i18n.mjs
 * 退出码：0 = 全部命中；1 = 有缺失/未命中
 */
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, join, resolve, extname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND = resolve(HERE, '..')
const REPO = resolve(FRONTEND, '..')
const SRC = join(FRONTEND, 'src')
const LANGS = ['zh', 'en', 'ja']

// ── 1. 解析字典 ────────────────────────────────────────────────
/** 提取 '...' 字符串时跳过转义引号 */
function matchBrace(text, openIdx) {
  let depth = 0
  let quote = null
  for (let i = openIdx; i < text.length; i++) {
    const c = text[i]
    if (quote) {
      if (c === '\\') i++
      else if (c === quote) quote = null
      continue
    }
    if (c === "'" || c === '"' || c === '`') { quote = c; continue }
    if (c === '{') depth++
    else if (c === '}') { depth--; if (depth === 0) return text.slice(openIdx, i + 1) }
  }
  throw new Error('未闭合的对象字面量')
}

/** 读一个 i18n 定义文件，返回 { ns: { lang: Set<key> } } */
function loadDictFile(file) {
  const text = readFileSync(file, 'utf8')
  const out = {}
  const re = /const\s+(\w+)\s*:\s*TranslationDict\s*=\s*\{/g
  let m
  while ((m = re.exec(text))) {
    const name = m[1]
    // 'zh' / 'en' / 'ja' = common 分区；'toolZh' / 'adminConfigEn' = 具名分区
    const isBare = ['zh', 'en', 'ja'].includes(name)
    const lang = isBare ? name : name.endsWith('Zh') ? 'zh' : name.endsWith('En') ? 'en' : name.endsWith('Ja') ? 'ja' : null
    const ns = isBare ? 'common' : name.slice(0, name.length - 2)
    if (!lang) continue
    const block = matchBrace(text, text.indexOf('{', m.index + m[0].length - 1))
    const obj = new Function('return (' + block + ')')()
    ;(out[ns] ||= {})[lang] = obj
  }
  return out
}

// 自动发现命名空间字典：src/i18n/ns/ 下一个 ns 一个文件，新增分区不用改本脚本。
// 每个文件导出 <ns>Zh / <ns>En / <ns>Ja，loadDictFile 从导出名反推 ns。
const NS_DIR = join(SRC, 'i18n', 'ns')
const NS_FILES = readdirSync(NS_DIR).filter(f => f.endsWith('.ts')).sort()
/** dicts[ns][lang] = { 'key': 'value' } */
const dicts = {}
for (const f of NS_FILES) {
  const loaded = loadDictFile(join(NS_DIR, f))
  for (const [ns, byLang] of Object.entries(loaded)) {
    dicts[ns] = { ...(dicts[ns] || {}), ...byLang }
  }
}
const NS = Object.keys(dicts)

// ── 1b. 注册表一致性 ──────────────────────────────────────────
// 字典文件存在 ≠ 运行时取得到：translations.ts 是唯一注册入口，漏登记的下场是整段裸 key，
// 而下面的比对只看字典文件、看不见这一层，所以单独核一遍。
const registrySrc = readFileSync(join(SRC, 'i18n', 'translations.ts'), 'utf8')
const registered = {}
for (const lang of LANGS) {
  const suffix = lang[0].toUpperCase() + lang.slice(1)
  const re = new RegExp(`([A-Za-z_]\\w*):\\s*[A-Za-z_]\\w*${suffix}\\b`, `g`)
  registered[lang] = new Set([...registrySrc.matchAll(re)].map(m => m[1]))
}
const unregistered = NS.filter(n => LANGS.some(l => !registered[l].has(n)))


// ── 2. 扫描源码里的调用点 ──────────────────────────────────────
/** 局部包装函数 → 命名空间（在组件内部把 ns 前缀写死的地方） */
const WRAPPERS = { tr: 'adminConfig' }

const usages = [] // { file, line, ns, key, kind }
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { walk(p); continue }
    if (!/\.(ts|tsx)$/.test(p) || p.includes(join('src', 'i18n'))) continue
    scanFile(p)
  }
}

function scanFile(file) {
  const text = readFileSync(file, 'utf8')
  // 匹配 t( / tr( 且前面不是标识符字符或点（排除 api.post( / .insert( 之类）
  const re = /(?<![\w$.'"])(t|tr)\(\s*/g
  let m
  while ((m = re.exec(text))) {
    const fn = m[1]
    const start = re.lastIndex
    const line = text.slice(0, m.index).split('\n').length
    const next = text[start]
    const splitNs = raw => {
      const i = raw.indexOf(':')
      return i > 0 ? { ns: raw.slice(0, i), key: raw.slice(i + 1) } : { ns: WRAPPERS[fn] || 'common', key: raw }
    }
    if (next === "'" || next === '"') {
      const end = text.indexOf(next, start + 1)
      usages.push({ file, line, ...splitNs(text.slice(start + 1, end)), kind: 'static' })
      re.lastIndex = end
    } else if (next === '`') {
      const end = text.indexOf('`', start + 1)
      const raw = text.slice(start + 1, end)
      usages.push({ file, line, ...splitNs(raw), raw, kind: raw.includes('${') ? 'template' : 'static' })
      re.lastIndex = end
    }
  }

  // 数据里存的 key（nameKey / descKey / labelKey）：t() 调用点扫不到它们，单独兜一遍，
  // 否则把文案 key 挪进常量表就等于给闸门开了个洞
  const keyRe = /(?:nameKey|descKey|labelKey)\s*:\s*['"]([^'"]+)['"]/g
  let k
  while ((k = keyRe.exec(text))) {
    const raw = k[1]
    // 只认长得像 key 的（带点、无空格）：labelKey 这个名字在别处另有所指（如 'JSON' 格式标签）
    if (!/^[A-Za-z_][\w]*\.[\w.]+$/.test(raw)) continue
    const i = raw.indexOf(':')
    usages.push({
      file,
      line: text.slice(0, k.index).split('\n').length,
      ns: i > 0 ? raw.slice(0, i) : 'common',
      key: i > 0 ? raw.slice(i + 1) : raw,
      kind: 'key-field',
    })
  }
}

walk(SRC)

// ── 3. 比对 ────────────────────────────────────────────────────
const missingAll = []   // 三语都缺 → 界面必然出现裸 key
const missingSome = []  // 部分缺 → 该语言会回退 zh 或显示裸 key
const dynamic = []
const seen = new Set()
for (const u of usages) {
  const { ns, key } = u
  if (u.kind === 'template') {
    // 模板串：把 ${...} 当通配，检查字典里有没有能匹配的 key
    const pattern = '^' + key.split(/\${[^}]*}/).map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^.]+?') + '$'
    const known = NS.includes(ns) ? Object.keys(dicts[ns][LANGS[0]] || {}) : []
    if (!known.some(k => new RegExp(pattern).test(ns === 'common' ? k : ns + ':' + k))) dynamic.push(u)
    continue
  }
  const id = ns + ':' + key
  const lacks = LANGS.filter(l => !(dicts[ns]?.[l] || {})[key])
  if (lacks.length) {
    const row = { ...u, id, lacks }
    if (lacks.length === LANGS.length) missingAll.push(row)
    else missingSome.push(row)
  }
  seen.add(id)
}

// ── 4. 后端下发的 i18n key（CONFIG_GROUPS 的 label_key / hint_key）──
// 前端 ConfigGroupCard 用 t(`adminConfig:${key}`) 渲染，静态扫不到，必须单独核对
const CONFIG_SCHEMA = join(REPO, 'backend/app/services/infrastructure/app_config_service.py')
if (existsSync(CONFIG_SCHEMA)) {
  const py = readFileSync(CONFIG_SCHEMA, 'utf8')
  for (const m of py.matchAll(/"(?:label_key|hint_key)":\s*"([^"]+)"/g)) {
    const lacks = LANGS.filter(l => !(dicts.adminConfig?.[l] || {})[m[1]])
    if (lacks.length) missingAll.push({ file: CONFIG_SCHEMA, line: py.slice(0, m.index).split('\n').length, ns: 'adminConfig', key: m[1], id: 'adminConfig:' + m[1], lacks })
  }
}

// ── 5. 反向：字典里有、源码里没直接用到的 key（仅供参考，模板串会误报） ──
const report = []
  if (unregistered.length) report.push('✗ 字典已存在但未在 translations.ts 注册（界面整段裸 key）:' + '\n' + unregistered.map(n => '  ' + n).join('\n'))
const fmt = r => `  ${r.file.replace(FRONTEND + '/', '')}:${r.line}  ${r.id ?? r.ns + ':' + r.key}${r.lacks ? '  [缺 ' + r.lacks.join('/') + ']' : ''}`
if (missingAll.length) report.push('✗ 三语都缺（界面会显示裸 key）:\n' + missingAll.map(fmt).join('\n'))
if (missingSome.length) report.push('✗ 部分语言缺:\n' + missingSome.map(fmt).join('\n'))
if (dynamic.length) report.push('? 模板串未能静态核验（' + dynamic.length + ' 处）:\n' + dynamic.map(fmt).join('\n'))

const counts = {}
for (const u of usages) counts[u.ns] = (counts[u.ns] || 0) + 1
report.push('统计: ' + Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(' ') + ` | 字典 ${NS.map(n => n + '=' + Object.keys(dicts[n].zh || {}).length).join(' ')}`)

if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ missingAll, missingSome, dynamic, unregistered }, null, 0))
} else {
  console.log(report.join('\n\n'))
}
process.exit(missingAll.length || missingSome.length || unregistered.length ? 1 : 0)
