// SPDX-License-Identifier: MIT
/**
 * 对账：后端「浏览器会直接访问的挂根前缀」 vs 前端的 proxy 表。
 *
 * 为什么需要它：这些路径不吃 /api 前缀，靠前端服务器按前缀转发到后端。漏一条，
 * 请求就落进 SPA fallback，用户看到平台的 404 页，而日志上后端一条请求都没有——
 * 这正是「打开 NapCat 协议控制台 404」的成因：面板前缀从 /qq-napcat 通用化成
 * /channels 之后，vite 的 proxy 还留着旧键，新键没人转发。
 *
 * 事实源只有一处：backend/app/utils/browser_paths.py 的 BROWSER_ROOT_PREFIXES。
 * 这里不再复制一份清单，只读它、和 vite.config.ts 的 proxy 键比对。
 * 依赖只用 node 内置模块：前端容器里没有 python，别把检查写成要先装解释器。
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '../..')
const SPEC = resolve(root, 'backend/app/utils/browser_paths.py')
const VITE = process.env.VITE_CONFIG || resolve(root, 'frontend/vite.config.ts')

/** 去掉 Python 的 # 行注释（字符串里的 # 目前不存在，够用） */
function stripPythonComments(text) {
  return text.replace(/#.*$/gm, '')
}

/** 读 BROWSER_ROOT_PREFIXES：支持元组里写常量名或字符串字面量 */
function backendPrefixes(source) {
  const text = stripPythonComments(source)
  const constants = new Map()
  for (const m of text.matchAll(/^([A-Z][A-Z0-9_]*)\s*=\s*"([^"]+)"/gm)) constants.set(m[1], m[2])
  const tuple = text.match(/BROWSER_ROOT_PREFIXES\s*=\s*\(([\s\S]*?)\)/)
  if (!tuple) throw new Error(`${SPEC} 里找不到 BROWSER_ROOT_PREFIXES`)
  return tuple[1]
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      if (item.startsWith('"') || item.startsWith("'")) return item.slice(1, -1)
      if (!constants.has(item)) throw new Error(`BROWSER_ROOT_PREFIXES 里的 ${item} 没有对应常量`)
      return constants.get(item)
    })
}

/** 取 proxy: { ... } 块里的键（按花括号配对找出块的结尾） */
function proxyKeys(source) {
  const start = source.indexOf('proxy: {')
  if (start < 0) throw new Error(`${VITE} 里找不到 proxy 配置`)
  let depth = 0
  let end = -1
  for (let i = source.indexOf('{', start); i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    else if (source[i] === '}') {
      depth -= 1
      if (depth === 0) {
        end = i
        break
      }
    }
  }
  if (end < 0) throw new Error(`${VITE} 的 proxy 块没有闭合`)
  const block = source.slice(start, end)
  return [...block.matchAll(/(?:'|")([^'"]+)(?:'|")\s*:\s*\{/g)].map((m) => m[1])
}

const normalize = (prefix) => prefix.replace(/\/+$/, '')

const backend = new Set(backendPrefixes(readFileSync(SPEC, 'utf8')).map(normalize))
const frontend = new Set(proxyKeys(readFileSync(VITE, 'utf8')).map(normalize))
// /api 是前端专用的那一层（转发时剥掉前缀），后端清单里本来就不该有它
frontend.delete('/api')

const missing = [...backend].filter((p) => !frontend.has(p))
const stale = [...frontend].filter((p) => !backend.has(p))

if (missing.length || stale.length) {
  console.error('proxy 前缀与后端清单对不上：')
  for (const p of missing) console.error(`  缺转发：${p}（后端有，vite 的 proxy 里没有 → 浏览器会 404）`)
  for (const p of stale) console.error(`  已失效：${p}（vite 的 proxy 里有，后端已没有这条路径）`)
  console.error('清单：backend/app/utils/browser_paths.py；proxy：frontend/vite.config.ts')
  process.exit(1)
}

console.log(`proxy 前缀一致（${[...backend].sort().join(' ')}）`)
