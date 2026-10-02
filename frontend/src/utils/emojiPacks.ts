/**
 * 表情包：把插件下发的表情表收成「写法 → 长相」，渲染层、输入框与预览浮窗共用这一份。
 *
 * 与后端 catalog 同一契约：标准表情在正文里就是 unicode 字符，自定义表情是 ASCII 短码 :id:，
 * 早期写法 [表情:名字] 仍可解析。此处只负责渲染，并提供三种出口：
 *   renderEmojiMarkers  Markdown 正文（图片输出 ![]()，交给渲染管线）
 *   renderEmojiPlain    只能拿到字符串的场景（通知正文、title）
 *   splitEmojiParts     JSX 场景（引用条、会话列表预览）按片段渲染
 * 未知写法一律原样保留，不吞正文。
 */
import { api } from '../api/client'

export interface EmojiFace {
  id: string
  name: string
  emoji: string
  file: string
}

export interface EmojiPack {
  id: string
  name: string
  usage: string
  faces: EmojiFace[]
}

/** JSX 渲染片段：有 file 就是一张表情图，否则是纯文本 */
export interface EmojiPart {
  text: string
  file?: string
  name?: string
}

// 正文里表情的两种规范写法（与后端 catalog 同一份契约）：
//   标准表情 = unicode 字符本身；自定义表情 = 业界惯例的 ASCII 短码 :id:
// [表情:名字] 是早期自造写法，只作兼容输入，历史消息仍能正常渲染。
const SHORTCODE_RE = /:([a-z][a-z0-9_-]{0,39}):/g
const LEGACY_RE = /\[表情:([^\]\n]{1,24})\]/g

let packs: EmojiPack[] = []
let loaded = false
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function notify() {
  listeners.forEach((fn) => fn())
}

/** 拉一次启用的表情包（模块级缓存：一屏气泡共用一个请求） */
export function loadEmojiPacks(): Promise<void> {
  if (loaded) return Promise.resolve()
  if (!inflight) {
    inflight = api.get<any>('/plugins')
      .then((res: any) => {
        packs = (res?.plugins || [])
          .filter((p: any) => p.category === 'emojipack' && (p.effective ?? p.global_enabled))
          .map((p: any): EmojiPack => ({
            id: p.id,
            name: p.name,
            usage: p.emoji_pack?.usage || '',
            faces: p.emoji_pack?.faces || [],
          }))
          .filter((p: EmojiPack) => p.faces.length > 0)
      })
      .catch(() => { packs = [] })      // 拿不到就当没装：写法照原样显示，不影响聊天
      .finally(() => { loaded = true; inflight = null; notify() })
  }
  return inflight
}

export function subscribeEmojiPacks(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function emojiPacks(): EmojiPack[] {
  return packs
}

/** 装了/卸了/关了表情包插件后调用：下次渲染重新拉一遍 */
export function invalidateEmojiPacks(): void {
  loaded = false
  inflight = null
  packs = []
  notify()
}

export function faceAssetUrl(pack: EmojiPack, face: EmojiFace): string {
  return `/api/plugins/${pack.id}/assets/${face.file}`
}

/** 输入框与 AI 该写的写法：有字符就用字符（跨通道都成立），只有图的用短码（短码是全局命名空间，与包无关） */
export function faceMarkup(face: EmojiFace): string {
  return face.emoji || `:${face.id}:`
}

/**
 * 按反引号 run 切段，偶数下标是"代码外"。
 *
 * 与后端 catalog._split_code 同一口径：run 必须找到等长收尾才算定界符，否则当普通字符——
 * 正文里一个落单的反引号不该把后面整段代码块翻成"代码外"。
 */
function codeSegments(content: string): Array<[boolean, string]> {
  const parts: Array<[boolean, string]> = []
  let pos = 0
  let scan = 0
  while (true) {
    const open = content.indexOf('`', scan)
    if (open < 0) {
      parts.push([false, content.slice(pos)])
      return parts
    }
    let run = 0
    while (content[open + run] === '`') run++
    const fence = '`'.repeat(run)
    const end = content.indexOf(fence, open + run)
    if (end < 0) {
      scan = open + run          // 没有等长收尾：当普通字符，继续往后找
      continue
    }
    parts.push([false, content.slice(pos, open)])
    parts.push([true, content.slice(open, end + run)])
    pos = scan = end + run
  }
}

let lookupCache: { source: EmojiPack[]; map: Map<string, [EmojiPack, EmojiFace]> } | null = null

function lookup(available: EmojiPack[]): Map<string, [EmojiPack, EmojiFace]> {
  if (lookupCache?.source === available) return lookupCache.map
  const map = new Map<string, [EmojiPack, EmojiFace]>()
  for (const pack of available) {
    for (const face of pack.faces) {
      // 短码为主、中文名为别名：短码跨包稳定，中文名供手打与兼容写法
      if (!map.has(face.name)) map.set(face.name, [pack, face])
      if (!map.has(face.id)) map.set(face.id, [pack, face])
    }
  }
  lookupCache = { source: available, map }
  return map
}

/** 只对代码外的片段做替换（代码里的 :x: 是在举例） */
function mapOutsideCode(content: string, fn: (segment: string) => string): string {
  return codeSegments(content)
    .map(([isCode, segment]) => (isCode ? segment : fn(segment)))
    .join('')
}

/** Markdown 正文：字符直接输出，仅图片的输出 Markdown 图片；没装包/认不出时原样返回 */
export function renderEmojiMarkers(content: string, available: EmojiPack[] = packs): string {
  if (!content || !available.length) return content
  if (!content.includes('[表情:') && !content.includes(':')) return content
  const index = lookup(available)
  const one = (key: string, whole: string) => {
    const hit = index.get(key.trim())
    if (!hit) return whole
    const [pack, face] = hit
    if (face.emoji) return face.emoji
    return face.file ? `![${face.name}](${faceAssetUrl(pack, face)})` : whole
  }
  return mapOutsideCode(content, (segment) => segment
    .replace(LEGACY_RE, (whole, key: string) => one(key, whole))
    .replace(SHORTCODE_RE, (whole, key: string) => one(key, whole)))
}

/** 只能拿到字符串的场景：字符照出，仅图片的退成名字，未知写法原样 */
export function renderEmojiPlain(content: string, available: EmojiPack[] = packs): string {
  if (!content || !available.length) return content
  if (!content.includes('[表情:') && !content.includes(':')) return content
  const index = lookup(available)
  const one = (key: string, whole: string) => {
    const hit = index.get(key.trim())
    if (!hit) return whole
    return hit[1].emoji || `[${hit[1].name}]`
  }
  return mapOutsideCode(content, (segment) => segment
    .replace(LEGACY_RE, (whole, key: string) => one(key, whole))
    .replace(SHORTCODE_RE, (whole, key: string) => one(key, whole)))
}

/** JSX 场景：切成片段交给调用方渲染（字符一段，表情图一段） */
export function splitEmojiParts(content: string, available: EmojiPack[] = packs): EmojiPart[] {
  if (!content || !available.length) return [{ text: content }]
  if (!content.includes('[表情:') && !content.includes(':')) return [{ text: content }]
  const index = lookup(available)
  const pattern = new RegExp(`${LEGACY_RE.source}|${SHORTCODE_RE.source}`, 'g')
  const parts: EmojiPart[] = []
  let buffer = ''
  const flush = () => {
    if (buffer) {
      parts.push({ text: buffer })
      buffer = ''
    }
  }
  for (const [isCode, segment] of codeSegments(content)) {
    if (isCode) {
      buffer += segment                    // 代码片段整段照旧
      continue
    }
    pattern.lastIndex = 0
    let last = 0
    let match: RegExpExecArray | null
    while ((match = pattern.exec(segment)) !== null) {
      const key = (match[1] ?? match[2] ?? '').trim()
      const hit = index.get(key)
      if (!hit) continue                   // 认不出：原样留在文本里
      const [pack, face] = hit
      buffer += segment.slice(last, match.index)
      if (face.file) {                     // 图片表情单独成段，由调用方画成小图
        flush()
        parts.push({ text: '', file: faceAssetUrl(pack, face), name: face.name })
      } else {
        buffer += face.emoji               // 字符表情并进文本
      }
      last = match.index + match[0].length
    }
    buffer += segment.slice(last)
  }
  flush()
  return parts.length ? parts : [{ text: content }]
}
