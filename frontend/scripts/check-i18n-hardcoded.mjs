#!/usr/bin/env node
/**
 * 硬编码文案闸（棘轮）
 *
 * 拦的是「用户能看见、却写死在源码里」的文案：JSX 文本、文案属性（title / placeholder /
 * aria-label …）、JSX 里直接写死的字符串分支。这类文字不会报错、不会崩，
 * 只是对非中文用户直接失效——和裸 key 一样，属于「没人发现就一直在」的问题。
 *
 * 四道 i18n 闸的分工：
 *   check-i18n.mjs           源码用了 key → 字典里有没有
 *   check-i18n-keys.mjs      动态 key 也解析（拿真字典跑一遍）
 *   check-i18n-parity.mjs    三语字典彼此是否对齐
 *   check-i18n-hardcoded.mjs（本文件）没用 key 的可见文案 → 逼你走 i18n
 *
 * 豁免分四层，只有第三层需要人写：
 *   1. 位置豁免（自动）—— 只审白名单里的位置，className / href / data-* 等根本不看
 *   2. 语言无关豁免（自动）—— UTF-8 / worlds/ / yyyy-MM-dd，判据在 lib/i18n-hardcoded-policy.mjs
 *   3. 声明豁免（人工）—— // i18n-exempt: 理由；理由必填，且该行必须真的命中问题
 *   4. 存量棘轮（机器生成）—— i18n-hardcoded-baseline.json，只允许下降
 *
 *   node scripts/check-i18n-hardcoded.mjs           # 闸门
 *   node scripts/check-i18n-hardcoded.mjs --list    # 列出存量，按文件迁移用
 *   node scripts/check-i18n-hardcoded.mjs --update  # 下调基线（升高会被拒绝）
 */
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import {
  TEXT_ATTRS,
  EXEMPT_MARKER,
  MIN_REASON_LENGTH,
  normalizeText,
  isLanguageNeutral,
} from './lib/i18n-hardcoded-policy.mjs'

const ROOT = path.resolve(import.meta.dirname, '..')
const SRC = path.join(ROOT, 'src')
const BASELINE = path.join(ROOT, 'i18n-hardcoded-baseline.json')
const update = process.argv.includes('--update')
const list = process.argv.includes('--list')

const rel = (file) => path.relative(ROOT, file).split(path.sep).join('/')

function* tsxFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules') yield* tsxFiles(full)
    } else if (entry.name.endsWith('.tsx')) {
      yield full
    }
  }
}

/** file → 源码行，收集阶段顺手缓存，后面做豁免与过期检查不再重复读盘 */
const rawLines = new Map()
const findings = []

function collectFile(file) {
  const src = fs.readFileSync(file, 'utf8')
  const name = rel(file)
  rawLines.set(name, src.split('\n'))
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1
  const pushAt = (node, text, kind, offset) => {
    const t = normalizeText(text)
    if (!t || isLanguageNeutral(t)) return
    findings.push({ file: name, line: lineOf(node.getStart(sf) + offset), text: t, kind })
  }
  /** 只下钻「一定是文案」的表达式形态；CallExpression 一律停住（t('key') 里是 key 不是文案） */
  const collectBranches = (node, kind) => {
    if (ts.isStringLiteralLike(node)) { pushAt(node, node.text, kind, 0); return }
    if (ts.isParenthesizedExpression(node)) { collectBranches(node.expression, kind); return }
    if (ts.isConditionalExpression(node)) {
      collectBranches(node.whenTrue, kind)
      collectBranches(node.whenFalse, kind)
      return
    }
    if (ts.isBinaryExpression(node)) {
      const k = node.operatorToken.kind
      if (k === ts.SyntaxKind.QuestionQuestionToken || k === ts.SyntaxKind.BarBarToken || k === ts.SyntaxKind.AmpersandAmpersandToken) {
        collectBranches(node.left, kind)
        collectBranches(node.right, kind)
      }
    }
  }
  const visit = (node) => {
    if (ts.isJsxText(node)) {
      const raw = node.getText(sf)
      const lead = raw.length - raw.trimStart().length
      pushAt(node, raw, 'jsx-text', lead)
    } else if (ts.isJsxAttribute(node)) {
      const attr = node.name.getText(sf)
      if (TEXT_ATTRS.has(attr) && node.initializer && ts.isStringLiteral(node.initializer)) {
        pushAt(node.initializer, node.initializer.text, 'attr:' + attr, 0)
      }
    } else if (ts.isJsxExpression(node) && node.expression) {
      // 属性里的表达式只看文案属性；className={a ? 'x' : 'y'} 这类是样式不是文案
      if (ts.isJsxAttribute(node.parent)) {
        const attr = node.parent.name.getText(sf)
        if (TEXT_ATTRS.has(attr)) collectBranches(node.expression, 'attr:' + attr)
      } else {
        collectBranches(node.expression, 'jsx-expr')
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
}

for (const file of tsxFiles(SRC)) collectFile(file)
findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line)

/** 声明豁免：// i18n-exempt: 理由 */
const reasonOf = (line) => {
  const i = line.indexOf(EXEMPT_MARKER)
  if (i < 0) return null
  return line
    .slice(i + EXEMPT_MARKER.length)
    .replace(/^[\s:：]+/, '')
    .replace(/\*\/.*$/, '')
    .trim()
}

const markers = new Map()
for (const [file, lines] of rawLines) {
  lines.forEach((line, i) => {
    const reason = reasonOf(line)
    if (reason === null) return
    if (!markers.has(file)) markers.set(file, new Map())
    markers.get(file).set(i + 1, reason)
  })
}

const kept = []
const badReasons = []
const usedMarkers = new Set()
for (const finding of findings) {
  const own = markers.get(finding.file)
  const at = own && (own.has(finding.line) ? finding.line : own.has(finding.line - 1) ? finding.line - 1 : null)
  if (!at) { kept.push(finding); continue }
  usedMarkers.add(finding.file + ':' + at)
  const reason = own.get(at)
  if (reason.length < MIN_REASON_LENGTH) badReasons.push({ ...finding, reason })
}

/** 登记了却没命中任何问题：说明文案已改走 i18n，或豁免挂错了位置 —— 逼你回来删 */
const expired = []
for (const [file, own] of markers) {
  for (const [line, reason] of own) {
    if (!usedMarkers.has(file + ':' + line)) expired.push({ file, line, reason })
  }
}

let baseline = {}
const initializing = !fs.existsSync(BASELINE)
if (!initializing) {
  try {
    baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8'))
  } catch {
    console.log('✗ 基线文件不是合法 JSON：' + path.relative(ROOT, BASELINE))
    process.exit(1)
  }
}

const counts = new Map()
for (const finding of kept) counts.set(finding.file, (counts.get(finding.file) || 0) + 1)

const filesOver = []
for (const [file, now] of counts) {
  const base = baseline[file] || 0
  if (now > base) filesOver.push({ file, base, now })
}
const reduced = Object.keys(baseline).filter((file) => (counts.get(file) || 0) < baseline[file])
const baseTotal = Object.values(baseline).reduce((a, b) => a + b, 0)

if (list) {
  for (const [file, now] of [...counts].sort()) {
    console.log(file + '  (' + (baseline[file] || 0) + ' → ' + now + ')')
    for (const finding of kept) {
      if (finding.file !== file) continue
      console.log('  ' + finding.line + '  [' + finding.kind + '] ' + finding.text)
    }
  }
  process.exit(0)
}

if (update) {
  if (expired.length || badReasons.length) {
    console.log('✗ 先把豁免处理干净再更新基线')
    for (const e of expired) console.log('  过期豁免 ' + e.file + ':' + e.line + '  ' + e.reason)
    for (const b of badReasons) console.log('  理由太短 ' + b.file + ':' + b.line)
    process.exit(1)
  }
  if (!initializing && filesOver.length) {
    console.log('✗ 以下文件的存量比基线多，--update 只允许下调：')
    for (const f of filesOver) console.log('  ' + f.file + '  基线 ' + f.base + ' → 当前 ' + f.now + '（新增 ' + (f.now - f.base) + '）')
    console.log('  新增的文案请改走 t()，确属语言无关或有意不翻就写 // i18n-exempt: 理由')
    process.exit(1)
  }
  const next = {}
  for (const file of [...counts.keys()].sort()) next[file] = counts.get(file)
  fs.writeFileSync(BASELINE, JSON.stringify(next, null, 2) + '\n')
  const total = Object.values(next).reduce((a, b) => a + b, 0)
  console.log('✓ 基线已' + (initializing ? '初始化' : '更新') + '：' + Object.keys(next).length + ' 个文件 / ' + total + ' 处')
  process.exit(0)
}

const report = []
if (expired.length) {
  report.push('✗ 过期豁免 ' + expired.length + ' 处（登记了却没命中问题，删掉或挪到正确行）:')
  for (const e of expired) report.push('  ' + e.file + ':' + e.line + '  ' + (e.reason || '(无理由)'))
}
if (badReasons.length) {
  report.push('✗ 豁免理由太短 ' + badReasons.length + ' 处（至少 ' + MIN_REASON_LENGTH + ' 个字符，写清为什么不该翻）:')
  for (const b of badReasons) report.push('  ' + b.file + ':' + b.line + '  ' + b.text)
}
if (filesOver.length) {
  report.push('✗ 有文件超出基线，新增了硬编码文案：')
  for (const f of filesOver) {
    report.push('  ' + f.file + '  基线 ' + f.base + ' → 当前 ' + f.now)
    for (const finding of kept) {
      if (finding.file !== f.file) continue
      report.push('    ' + finding.line + '  [' + finding.kind + '] ' + finding.text)
    }
  }
  report.push('  修法：用 t(ns:key) 走 i18n；确属语言无关（UTF-8、worlds/、yyyy-MM-dd）或有意不翻，')
  report.push('        就在上一行写 // i18n-exempt: 理由。存量整体下调用 --update。')
}

const total = kept.length
const header = '硬编码文案闸：' + rawLines.size + ' 个 .tsx / 基线内 ' + baseTotal + ' 处 / 当前 ' + total + ' 处'
if (report.length) {
  console.log(header)
  console.log('')
  console.log(report.join('\n'))
  process.exit(1)
}
console.log('✓ ' + header + '，新增 0')
if (reduced.length) console.log('  提示：' + reduced.length + ' 个文件的存量下降，可跑 --update 下调基线')
