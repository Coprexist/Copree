#!/usr/bin/env node
/**
 * UI 标准闸门（棘轮）
 *
 * 全站视觉只有一个来源：index.css 的语义类（.btn / .field / .icon-btn / .card / .chip）
 * 加 components/ui 的 React 外壳。组件只是语义类的外壳，所以两条路都算合格：
 * 用 Button / Input / Select，或者手写标签直接挂 .btn / .field。
 *
 * 命中即算违规：
 *   1. 按钮自带内边距却没有 .btn / .icon-btn
 *   2. input / select / textarea 自带内边距却没有 .field
 *   3. 挂了 .btn / .field 又自己写 rounded-*（形状归档位，不归元素）
 *
 * 存量迁移用棘轮机制：ui-baseline.json 记着每个文件当前的违规数，只允许不增加——
 * 新代码多写一个手写控件立刻红；每迁完一批用 --update 下调基线。
 *
 * 确有例外（拖拽把手、整块可点的自定义卡片这类不是按钮形态的），
 * 在标签前一行或同一行写 ui-standard-ignore 并说明原因。
 */
import { readdirSync, readFileSync, statSync, writeFileSync, existsSync } from 'node:fs'
import { join, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src')
const BASELINE = join(ROOT, 'ui-baseline.json')
const IGNORE = 'ui-standard-ignore'
const PAD = new RegExp('\\b(?:p|px|py|pt|pb|pl|pr)-\\d')
const update = process.argv.includes('--update')
/** --list 逐条打印存量（可选 --only=<路径片段> 只看某个文件/目录）：按页迁移时用 */
const list = process.argv.includes('--list')
const only = (process.argv.find((a) => a.startsWith('--only=')) || '').slice('--only='.length)

const problems = []

/** 从标签起点扫到它自己的 >，跳过字符串字面量与 {} 表达式里的括号 */
function tagAt(src, start) {
  let i = start, depth = 0, quote = null
  while (i < src.length) {
    const c = src[i]
    if (quote) {
      if (c === '\\') { i += 2; continue }
      if (c === quote) quote = null
      i++
      continue
    }
    if (c === '"' || c === "'" || c === String.fromCharCode(96)) { quote = c; i++; continue }
    if (c === '{') { depth++; i++; continue }
    if (c === '}') { depth--; i++; continue }
    if (c === '>' && depth === 0) return src.slice(start, i + 1)
    i++
  }
  return src.slice(start)
}

const TAG_RE = new RegExp('<(button|input|select|textarea)\\b', 'g')
const CLASS_RE = new RegExp('className=\\{?(["\'' + String.fromCharCode(96) + '])([\\s\\S]*?)\\1')

function scan(path) {
  const src = readFileSync(path, 'utf8')
  const startLines = src.split('\n')
  TAG_RE.lastIndex = 0
  let m
  while ((m = TAG_RE.exec(src))) {
    const tag = m[1]
    const text = tagAt(src, m.index)
    const line = src.slice(0, m.index).split('\n').length
    if (text.includes(IGNORE) || (startLines[line - 2] || '').includes(IGNORE)) continue
    const found = CLASS_RE.exec(text)
    const cls = found ? found[2] : ''
    // 密钥 / Token 框不许标成密码：浏览器会当成账号密码自动填充、弹「保存密码」
    if (/type="password"/.test(text)) {
      problems.push([path, line, '密钥/Token 框不要用 type="password"：改用 SecretInput（真密码请加 ui-standard-ignore）'])
      continue
    }
    if (!PAD.test(cls)) continue
    const isButton = tag === 'button'
    const standard = isButton
      ? new RegExp('\\bbtn\\b|\\bicon-btn\\b').test(cls)
      : new RegExp('\\bfield\\b').test(cls)
    if (!standard) {
      problems.push([path, line, isButton
        ? '手写按钮（无 .btn）：改用 Button / IconButton，或直接挂 .btn'
        : '手写输入框（无 .field）：改用 Input / Select，或直接挂 .field'])
      continue
    }
    if (new RegExp('(?:^|\\s)rounded-(?!none)').test(cls)) {
      problems.push([path, line, isButton
        ? '按钮自己写了 rounded-*：胶囊归 .btn-sm/.btn-xs，圆角归 .btn-md/.btn-lg'
        : '输入框自己写了 rounded-*：形状归 .field / .field-sm'])
    }
  }
}

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (name === 'node_modules') continue
    if (statSync(path).isDirectory()) {
      if (path === join(SRC, 'components', 'ui')) continue
      walk(path)
      continue
    }
    if (new RegExp('\\.tsx?$').test(name)) scan(path)
  }
}

walk(SRC)

const counts = {}
for (const [path] of problems) {
  const rel = relative(ROOT, path)
  counts[rel] = (counts[rel] || 0) + 1
}
const total = problems.length

if (list) {
  const rows = problems
    .map(([p, line, msg]) => [relative(ROOT, p), line, msg])
    .filter(([rel]) => !only || rel.includes(only))
  for (const [rel, line, msg] of rows) console.log(rel + ':' + line + '  ' + msg)
  console.log('')
  console.log('共 ' + rows.length + ' 处')
  process.exit(0)
}

if (update) {
  const files = Object.fromEntries(Object.entries(counts).sort())
  writeFileSync(BASELINE, JSON.stringify({ total, files }, null, 2) + '\n')
  console.log('已重写基线：' + total + ' 处，分布在 ' + Object.keys(files).length + ' 个文件。')
  process.exit(0)
}

const baseline = existsSync(BASELINE)
  ? JSON.parse(readFileSync(BASELINE, 'utf8'))
  : { total: 0, files: {} }

const added = []
for (const [rel, n] of Object.entries(counts)) {
  const was = baseline.files[rel] || 0
  if (n > was) added.push([rel, was, n])
}

if (added.length || total > baseline.total) {
  const addedFiles = new Set(added.map(([rel]) => rel))
  console.log('')
  console.log('UI 标准：新增了手写控件（基线只减不增）')
  for (const [rel, was, now] of added) console.log('  ' + rel + '：' + was + ' -> ' + now)
  for (const [p, line, msg] of problems) {
    const rel = relative(ROOT, p)
    if (addedFiles.has(rel)) console.log('    ' + rel + ':' + line + '  ' + msg)
  }
  console.log('')
  console.log('总数 ' + total + '，基线 ' + baseline.total + '。改完跑 npm run ui:check:update 下调基线。')
  process.exit(1)
}

if (total < baseline.total) {
  console.log('UI 标准通过，且比基线少了 ' + (baseline.total - total) + ' 处（' + total + ' / ' + baseline.total + '）。')
  console.log('迁移完可以跑 npm run ui:check:update 把基线调到 ' + total + '。')
} else {
  console.log('UI 标准通过（' + total + ' 处存量，基线 ' + baseline.total + '）。')
}