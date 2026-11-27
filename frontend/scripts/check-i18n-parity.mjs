#!/usr/bin/env node
/**
 * i18n 对齐闸门：三语字典**自身**能不能互相对上。
 *
 * 与另外两条的分工（它们都从「源码里用了什么」出发）：
 *   check-i18n.mjs       源码里的 t('x.y') / *Key 字段 → 字典里有没有
 *   check-i18n-keys.mjs  调用点拿去真字典解析一遍，顺带捞出点号形式的漏网
 *   本条                 字典 → 字典：**没人调用就永远发现不了**的那一类错
 *
 * 为什么需要它：getTranslation() 查不到 key 就原样回显，查到了就照发。于是
 * 「en 少了某个键」「ja 的占位符比 zh 少一个 {count}」都不会崩、不会报错，
 * 只在某一种语言的界面上静默变形——而这类错没有调用点可循，只能三语互相比。
 *
 * 规则：
 *   1. 每个命名空间的键集合三语一致（缺键 / 多键）
 *   2. 同一个键的占位符集合三语一致（{name} / {count}）
 *   3. 值必须是非空字符串（纯空白算空）
 *   4. EXEMPT 里登记的不算问题；但登记了却不再命中问题＝过期例外，同样报错
 *
 * 用法（frontend/ 目录）：node scripts/check-i18n-parity.mjs
 * 退出码：0 = 三语对齐；1 = 有不一致
 */
import path from 'node:path'
import { loadDicts, LANGS } from './lib/i18n-dicts.mjs'

const NS_DIR = path.join(path.resolve(import.meta.dirname, '..'), 'src/i18n/ns')

/**
 * 确认不是问题的例外：'ns:key' → 理由。
 *
 * 只登记「三语本来就不该一致」的情况。登记后仍必须在本轮命中一次，
 * 否则说明文案已经改好、例外该删了——避免例外表烂成一张免死金牌清单。
 */
const EXEMPT = {
  'admin:emailTemplateVarHint':
    '文案本身在给用户示范「用 {变量名} 这种格式」，不是运行时插值；三语各按自己的语言写示意（zh 写 {变量名}、en 写 {variable}），所以占位符集合天然不同',
}

/** 取出字符串里的 {占位符}；只认简单的 ASCII 标识符，中文示意（{变量名}）不算占位符 */
function placeholders(value) {
  const found = new Set()
  if (typeof value !== 'string') return found
  let i = 0
  while ((i = value.indexOf('{', i)) !== -1) {
    const end = value.indexOf('}', i)
    if (end === -1) break
    const name = value.slice(i + 1, end)
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) found.add(name)
    i = end + 1
  }
  return found
}

const dicts = await loadDicts(NS_DIR)
const NS = Object.keys(dicts.zh).sort()

const problems = []
const hitExempt = new Set()
let keyCount = 0

for (const ns of NS) {
  const byLang = { zh: dicts.zh[ns], en: dicts.en[ns], ja: dicts.ja[ns] }
  const absentLang = LANGS.filter((l) => !byLang[l])
  if (absentLang.length) {
    problems.push({ id: ns, msg: '缺 ' + absentLang.join('/') + ' 整块字典' })
    continue
  }

  const keys = new Set()
  for (const lang of LANGS) for (const key of Object.keys(byLang[lang])) keys.add(key)

  for (const key of [...keys].sort()) {
    keyCount++
    const id = ns + ':' + key
    const absent = LANGS.filter((l) => !(key in byLang[l]))
    const bad = []
    if (absent.length) {
      bad.push(absent.join('/') + ' 缺这个键')
    } else {
      const sets = {}
      for (const lang of LANGS) sets[lang] = [...placeholders(byLang[lang][key])].sort().join(',')
      if (new Set(LANGS.map((l) => sets[l])).size > 1) {
        bad.push('占位符不一致（' + LANGS.map((l) => l + ' {' + sets[l] + '}').join(' / ') + '）')
      }
      for (const lang of LANGS) {
        const value = byLang[lang][key]
        if (typeof value !== 'string') bad.push(lang + ' 的值不是字符串（' + typeof value + '）')
        else if (value.trim() === '') bad.push(lang + ' 的值是空/纯空白')
      }
    }
    if (!bad.length) continue
    if (EXEMPT[id]) { hitExempt.add(id); continue }
    problems.push({ id, msg: bad.join('；') })
  }
}

for (const id of Object.keys(EXEMPT)) {
  if (!hitExempt.has(id)) problems.push({ id, msg: '例外已不再命中任何问题，请从 EXEMPT 删除' })
}

if (problems.length) {
  console.log('✗ i18n 三语不一致 ' + problems.length + ' 处：')
  for (const p of problems) console.log('  ' + p.id + '  ' + p.msg)
  console.log('')
  console.log('修法：把缺的键按其它两种语言的文案补齐（占位符要一一对应）；确实不该一致的，')
  console.log('      在 check-i18n-parity.mjs 的 EXEMPT 里登记并写清理由。')
  process.exit(1)
}

const exemptNote = hitExempt.size ? '，' + hitExempt.size + ' 处已登记例外' : ''
console.log('✓ 三语对齐：' + NS.length + ' 个命名空间 / ' + keyCount + ' 个键' + exemptNote)
