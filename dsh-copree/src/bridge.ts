// SPDX-License-Identifier: MIT
/**
 * dsh-copree — Copree 反向桥接（Copree 管理端 → DSH 本体会话）。
 *
 * 方向与 \`/copree-api\`（DSH 浏览器 → Copree 后端）相反：这里让 Copree 管理端把
 * 一条消息推进 **DSH 自己的会话**，再把该会话的持久事件流回放过去。真正跑对话、
 * 跑工具、改代码的始终是 DSH 本体的 sessionController；本模块只做「鉴权 + 帧翻译」，
 * 不持有会话状态，也不复制 DSH 的渲染管线。
 *
 * 安全姿态：
 * - 全部路由要求 \`x-copree-bridge-token\`，与配置的 bridgeSecret 恒定时间比较；
 *   未配置密钥时整条桥接不注册——宁可没有，也不开一个能改代码的匿名口子。
 * - 只暴露必要的会话操作：列表 / 新建并投递 / 跟随事件 / 取消；不做删除、不做设置。
 * - 帧翻译只取可见文本与工具名，模型思考、工具内部 meta、附件字节一律不出境。
 *
 * 可注册性由心跳维持：插件每 HEARTBEAT_MS 向 Copree 后端注册一次（带本插件的
 * Copree 可达地址），Copree 侧据此显示「运行中」并把管理端的请求转回来。
 */
import type { Context } from '@deepseek-ai/cordis'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { randomUUID, timingSafeEqual } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { homedir, tmpdir } from 'node:os'
import { readJsonBody, sendJson } from './http.js'

/** 桥接路由前缀：这个前缀的调用方是 Copree，不是浏览器。 */
export const BRIDGE_PREFIX = '/copree-bridge'

/** 心跳间隔。Copree 侧 TTL 取其三倍，容忍一次网络抖动。 */
export const HEARTBEAT_MS = 20_000

/**
 * 一帧桥接事件——词表刻意很小，只够 Copree 画出一条能读的对话：
 * 谁的输入、逐步的助手正文、工具调用与结果、一轮结束。
 */
export type BridgeFrame =
  | { k: 'snapshot'; records: BridgeFrame[] }
  | { k: 'user'; text: string }
  | { k: 'step'; turn: number; step: number }
  // live = 这一帧来自直播增量（还有后续增量会接上），不是落库的完整文本。
  // 页面靠它区分「增量接在后面」与「权威文本覆盖」，否则同一段话会出现两遍。
  | { k: 'delta'; text: string; live?: boolean }
  | { k: 'think'; text: string; live?: boolean }
  | { k: 'say'; text: string; interrupted?: boolean }
  // 工具帧的标签与子操作都来自工具自己声明的卡片（card/title/kind），不是桥接猜的
  | { k: 'tool'; callId: string; name: string; args: string; card: string; title: string; kind: string; detail: string }
  | { k: 'toolDone'; callId: string; ok: boolean; summary: string; lines: string[] }
  | { k: 'turnEnd'; reason: string }
  | { k: 'error'; message: string }
  // 会话里的 AI 在等人：审批（要动手前的许可）与提问（ask_user_question）。
  // 这两种必须过桥，否则 DSH 侧会一直等着，而人在 Copree 页面里什么都看不到。
  | { k: 'ask'; ask: AskRequest }
  | { k: 'askDone'; id: string }

/** 单帧文本上限：桥接是给人看的镜像，不做无上限搬运。 */
const TEXT_LIMIT = 20_000
const ARGS_LIMIT = 2_000
const SUMMARY_LIMIT = 400

// ── 图片附件（Copree → DSH 的会话） ──────────────────────────────────────────
/**
 * 桥接本身只搬文本，图片走的是"前端把图片编码进正文、本插件在落地一刻把它换成文件路径"
 * 的办法：会话里的 AI 用自己的看图工具去读，DSH 的会话记录里也不会留一坨 base64。
 * 围栏名与 Copree 前端 `utils/dshAttachments.ts` 成对，改动要一起改。
 */
const ATTACHMENT_FENCE = 'copree-attachments'
/** 单张图片解码后的字节上限：桥接不做无上限搬运 */
const MAX_IMAGE_BYTES = 8 * 1024 * 1024
/** 一条消息最多几张：再多就该先自己压一压 */
const MAX_IMAGES_PER_PROMPT = 8
/** /prompt 的请求体上限：base64 会膨胀 4/3，按"8 张各自压到几 MB"留余量 */
const PROMPT_BODY_LIMIT = 48 * 1024 * 1024

// ── 人在哪：DSH 侧的「等你回答」（审批 / 提问）────────────────────────────
/**
 * 为什么必须有这一段：DSH 的审批与 ask_user_question 都是**阻塞式**的——没人回答，
 * 会话就停在那里。Copree 页面不接这个链子，人只会看到"AI 不动了"。
 * 所以本插件把自己注册进 DSH 的两条回答者瀑布，把请求变成一帧发给正在看这条会话的
 * Copree 页面，并把页面回的决定交还给 DSH。
 *
 * 没人在看（该会话没有打开的流）时**立刻交回默认链**：宁可让 DSH 按它自己的
 * fail-closed 规则办，也不能把会话挂死在一个没人看的弹窗上。
 */
export interface AskOption { label: string; description?: string }
export interface AskQuestion {
  id: string
  question: string
  detail?: string
  header?: string
  options?: AskOption[]
  multiSelect?: boolean
}
export interface AskRequest {
  id: string
  sessionId: string
  kind: 'approval' | 'question'
  /** 审批：要动手的工具名与它自己写的理由 */
  toolName?: string
  reason?: string
  /** 提问：逐条问题（含选项） */
  questions?: AskQuestion[]
  createdAt: number
}
export interface AskDecision {
  /** 审批：同意与否 */
  approve?: boolean
  /** 提问：结构化答案 */
  answers?: { id: string; selected: string[]; custom?: string }[]
}

/** 等人回答的窗口：与群视界审批的 10 分钟同口径，超时就把决定权交回 DSH */
const ASK_TIMEOUT_MS = 10 * 60_000

class AskBroker {
  private streams = new Map<string, Set<(frame: BridgeFrame) => void>>()
  private pending = new Map<string, { ask: AskRequest; resolve: (d: AskDecision | null) => void; timer: NodeJS.Timeout }>()

  /** 某个会话开了流：此后它就有"人在看"，新请求才会往 Copree 送 */
  subscribe(sessionId: string, write: (frame: BridgeFrame) => void): () => void {
    let set = this.streams.get(sessionId)
    if (!set) { set = new Set(); this.streams.set(sessionId, set) }
    set.add(write)
    // 订阅即刻补发该会话尚未答完的请求：页面刷新/重连不会把弹窗丢掉
    for (const { ask } of this.pending.values()) {
      if (ask.sessionId === sessionId) write({ k: 'ask', ask })
    }
    return () => {
      const current = this.streams.get(sessionId)
      if (!current) return
      current.delete(write)
      if (current.size === 0) this.streams.delete(sessionId)
    }
  }

  /** 把请求送出去并等回答；没人在看返回 null（调用方据此交回默认链） */
  ask(ask: AskRequest): Promise<AskDecision | null> {
    const audience = this.streams.get(ask.sessionId)
    if (!audience || audience.size === 0) return Promise.resolve(null)
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.settle(ask.id, null) }, ASK_TIMEOUT_MS)
      timer.unref?.()
      this.pending.set(ask.id, { ask, resolve, timer })
      for (const write of audience) write({ k: 'ask', ask })
    })
  }

  /**
   * 当前哪些会话开着流（谁在看）——诊断用。
   * 提问帧只发给「正在看这条会话」的页面，没有观众就回落 DSH 默认链；
   * 排查「我在页面里没看到提问」时，第一眼要能看出观众到底有没有。
   */
  watchers(): { sessionId: string; subscribers: number }[] {
    return [...this.streams.entries()].map(([sessionId, set]) => ({ sessionId, subscribers: set.size }))
  }

  /** 页面回的决定：命中即唤醒等待方，并通知所有在看的页面把弹窗收掉 */
  answer(id: string, decision: AskDecision): boolean {
    return this.settle(id, decision)
  }

  private settle(id: string, decision: AskDecision | null): boolean {
    const entry = this.pending.get(id)
    if (!entry) return false
    this.pending.delete(id)
    clearTimeout(entry.timer)
    const audience = this.streams.get(entry.ask.sessionId)
    if (audience) for (const write of audience) write({ k: 'askDone', id })
    entry.resolve(decision)
    return true
  }
}

/** 会话级的问询登记处：桥接路由与回答者瀑布共用这一个实例 */
export const askBroker = new AskBroker()

/** 提问请求 → 帧负载：只取展示需要的字段，agent 等内部结构不出境 */
function questionsOf(raw: unknown): AskQuestion[] {
  if (!Array.isArray(raw)) return []
  return raw.map((q: any) => ({
    id: String(q?.id ?? ''),
    question: String(q?.question ?? ''),
    ...(q?.detail ? { detail: String(q.detail) } : {}),
    ...(q?.header ? { header: String(q.header) } : {}),
    ...(Array.isArray(q?.options)
      ? { options: q.options.map((o: any) => ({ label: String(o?.label ?? ''), ...(o?.description ? { description: String(o.description) } : {}) })) }
      : {}),
    ...(q?.multiSelect === true ? { multiSelect: true } : {}),
  }))
}

/**
 * 把自己注册成 DSH 的回答者：请求来了问 Copree 页面，页面答了再交回 DSH。
 * @returns disposer（插件卸载时摘掉两个监听）
 */
export function registerAnswerers(ctx: Context): () => void {
  const anyCtx = ctx as any
  if (typeof anyCtx?.on !== 'function') return () => {}

  const onApproval = async (req: any, next: () => Promise<any>): Promise<any> => {
    const sessionId = String(req?.agent?.sessionId ?? '')
    if (!sessionId) return next()
    const decision = await askBroker.ask({
      id: randomUUID(),
      sessionId,
      kind: 'approval',
      toolName: String(req?.toolName ?? ''),
      ...(req?.reason ? { reason: String(req.reason) } : {}),
      createdAt: Date.now(),
    })
    if (!decision) return next()
    return decision.approve === true ? 'allowed-once' : 'rejected'
  }
  const onQuestions = async (request: any, next: () => Promise<any>): Promise<any> => {
    const sessionId = String(request?.agent?.sessionId ?? '')
    if (!sessionId) return next()
    const decision = await askBroker.ask({
      id: randomUUID(),
      sessionId,
      kind: 'question',
      questions: questionsOf(request?.questions),
      createdAt: Date.now(),
    })
    if (!decision) return next()
    return { answers: decision.answers ?? [] }
  }

  anyCtx.on('approval/request', onApproval)
  anyCtx.on('user-questions/request', onQuestions)
  return () => {
    anyCtx.off?.('approval/request', onApproval)
    anyCtx.off?.('user-questions/request', onQuestions)
  }
}

/** 前端塞进正文的一张图片 */
export interface InlineImage {
  name: string
  mime: string
  /** 纯 base64（不带 data: 前缀） */
  data: string
}

/**
 * 从正文里切出附件块并解析；顺带把围栏从正文里删掉（base64 不该进模型上下文）。
 * 没带附件块时**一个字都不动**：桥接不该借"清理"之名改写用户原话。
 */
export function splitInlineImages(text: string): { text: string; images: InlineImage[] } {
  const images: InlineImage[] = []
  let fenced = false
  const fence = new RegExp(`<${ATTACHMENT_FENCE}>([\\s\\S]*?)</${ATTACHMENT_FENCE}>`, 'g')
  const cleaned = text.replace(fence, (_all, payload: string) => {
    fenced = true
    try {
      const parsed = JSON.parse(payload)
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          const mime = String(item?.mime ?? '')
          const data = item?.data
          if (mime.startsWith('image/') && typeof data === 'string' && data.length > 0) {
            images.push({ name: String(item?.name ?? 'image'), mime, data })
          }
        }
      }
    } catch { /* 解析不了就只当没带图：脏数据不该把整条消息顶掉 */ }
    return ''
  })
  if (!fenced) return { text, images: [] }
  // 围栏挖掉后常留下连续空行：只在真的动过正文时才收拾
  return { text: cleaned.replace(/\n{3,}/g, '\n\n').trim(), images }
}

/**
 * 落盘目录：优先会话工作区（DSH 沙箱写得进、会话里的 AI 也读得到），
 * 拿不到工作区就退到系统临时目录——两条路都在同一个文件系统上，AI 都读得到。
 */
function attachmentDir(cwd: string): string {
  return join(cwd || tmpdir(), '.copree', 'attachments')
}

/** 把图片写成文件，返回绝对路径；超限的按张跳过并计数（不静默丢弃） */
export async function saveInlineImages(images: InlineImage[], cwd: string): Promise<{ saved: string[]; skipped: number }> {
  const saved: string[] = []
  let skipped = Math.max(0, images.length - MAX_IMAGES_PER_PROMPT)
  const dir = attachmentDir(cwd)
  await mkdir(dir, { recursive: true })
  for (const img of images.slice(0, MAX_IMAGES_PER_PROMPT)) {
    const buf = Buffer.from(img.data, 'base64')
    if (buf.length === 0 || buf.length > MAX_IMAGE_BYTES) { skipped += 1; continue }
    // 文件名只留一段：不让外部输入决定落点；前缀时间戳与序号，避免同名互相覆盖
    const safe = basename(img.name).replace(/[^\w.\-]+/g, '_').slice(-80) || 'image'
    const file = join(dir, `${Date.now()}-${saved.length}-${safe}`)
    await writeFile(file, buf)
    saved.push(file)
  }
  return { saved, skipped }
}

/** 前端只送引用时的一张附件（正文里因此不再有 base64）。 */
export interface AttachmentRef {
  fileId: number
  name: string
  mime: string
}

/**
 * 按 fileId 从 Copree 后端把字节取回来落盘。
 *
 * 为什么不再让前端把 base64 塞进正文：一张截图就能把请求撑到几 MB，链路上任何一跳
 * （反向代理默认 1MB 等）都会先把它掐掉，用户看到的就是「只剩文件名、图没过来」。
 * 取字节改走「本插件 ↔ Copree 后端」的直连（密钥认证），正文永远只有一行标记。
 */
export async function pullAttachments(
  refs: AttachmentRef[],
  cwd: string,
  source: { backendUrl: string; secret: string },
): Promise<{ saved: string[]; skipped: number }> {
  const saved: string[] = []
  let skipped = Math.max(0, refs.length - MAX_IMAGES_PER_PROMPT)
  const dir = attachmentDir(cwd)
  await mkdir(dir, { recursive: true })
  for (const ref of refs.slice(0, MAX_IMAGES_PER_PROMPT)) {
    const fileId = Number(ref?.fileId)
    if (!Number.isInteger(fileId) || fileId <= 0) { skipped += 1; continue }
    try {
      const response = await fetch(`${source.backendUrl}/dsh-bridge/attachment/${fileId}`, {
        headers: { 'x-copree-bridge-token': source.secret },
        signal: AbortSignal.timeout(30_000),
      })
      if (!response.ok) { skipped += 1; continue }
      const buf = Buffer.from(await response.arrayBuffer())
      if (buf.length === 0 || buf.length > MAX_IMAGE_BYTES) { skipped += 1; continue }
      const safe = basename(String(ref.name ?? 'image')).replace(/[^\w.\-]+/g, '_').slice(-80) || 'image'
      const file = join(dir, `${Date.now()}-${saved.length}-${safe}`)
      await writeFile(file, buf)
      saved.push(file)
    } catch {
      // 单张取失败不拖垮整条消息：计数后继续，正文里会如实说明
      skipped += 1
    }
  }
  return { saved, skipped }
}

/** 从消息 content 里取可见文本（其余块类型不桥接）。 */
function textOf(content: unknown): string {
  if (!Array.isArray(content)) return ''
  return content
    .filter((block: any) => block?.type === 'text' && typeof block.text === 'string')
    .map((block: any) => block.text as string)
    .join('')
    .slice(0, TEXT_LIMIT)
}

/** 工具结果：是否失败 + 一段可读摘要（来自 tool-result 块的文本内容）。 */
function toolResultOf(data: any): { callId: string; ok: boolean; summary: string } {
  const block = Array.isArray(data?.message?.content) ? data.message.content[0] : undefined
  const callId = String(data?.message?.source?.callId ?? block?.toolCallId ?? '')
  const ok = data?.error === undefined && block?.isError !== true
  const text = textOf(block?.content).replace(/\s+/g, ' ').trim()
  return { callId, ok, summary: text.slice(0, SUMMARY_LIMIT) }
}

/** 从消息 content 里取思考文本（reasoning 块；DSH 自己的界面也把它折叠显示）。 */
function reasoningOf(content: unknown): string {
  if (!Array.isArray(content)) return ''
  return content
    .filter((block: any) => block?.type === 'reasoning' && typeof block.text === 'string')
    .map((block: any) => block.text as string)
    .join('')
    .slice(0, TEXT_LIMIT)
}

/**
 * 工具卡片：DSH 的工具是**声明式 presentation**（\`ToolDefinition.presentCall/presentResult\`），
 * 文档原话是 UI 不该按工具名特判。PTC 模式下 \`run_code\` 这类复合调用的子操作
 * （编辑/读取/命令）也只在声明里，所以桥接把声明搬过去，而不是自己猜标签。
 */
const argsByCallId = new Map<string, { name: string; args: unknown }>()
/**
 * 出现过子操作的复合调用（PTC 的 run_code）：它的聚合输出已经把子操作的结果包含了一遍，
 * 父行再铺一遍就是同一份内容出现两次，所以父行只留标题。
 */
const parentsWithSubOps = new Set<string>()
/** 完成态最多铺几行：卡片是给人看的，不做无上限搬运 */
const MAX_RESULT_LINES = 12

/**
 * 取工具定义：先按会话 scope 查（agent 平面的工具只在那里可见），
 * 再退回全局视图（PTC 模式下 run_code 这类 transport 由可见性解析器补上，全局也拿得到）。
 * 两处都拿不到就返回 undefined，交给调用方退化成通用卡片。
 */
function definitionFor(tools: any, name: string, scope?: unknown): any {
  if (!name) return undefined
  try { const scoped = tools?.get?.(name, scope); if (scoped) return scoped } catch { /* 继续退回全局 */ }
  try { return tools?.get?.(name) } catch { return undefined }
}

/** 把一次调用交给工具自己的 presenter；未声明或抛错时退回通用卡片。 */
export function presentCallOf(tools: any, name: string, args: unknown, scope?: unknown): { card: string; title: string; kind: string; detail: string } {
  let view: any
  // scope 不能省：agent 平面的工具（run_code/read/edit/bash…）在全局视图里看不见，
  // 不传 scope 会一律退化成通用卡片 —— 表现就是「标签只有工具名、子操作也没有」。
  try { view = definitionFor(tools, name, scope)?.presentCall?.(args) } catch { view = undefined }
  const rawArgs = typeof args === 'string' ? args : JSON.stringify(args ?? {})
  if (!view || typeof view !== 'object') {
    return { card: 'generic', title: name, kind: 'other', detail: String(rawArgs ?? '').slice(0, ARGS_LIMIT) }
  }
  const detail = view.card === 'diff'
    ? (Array.isArray(view.diffs) ? view.diffs.map((d: any) => String(d?.path ?? '')).filter(Boolean).join('\n') : '')
    : typeof view.rawInput === 'string'
      ? view.rawInput
      : JSON.stringify(view.rawInput ?? args ?? {})
  return {
    card: String(view.card ?? 'generic'),
    title: String(view.title ?? name),
    kind: String(view.kind ?? 'other'),
    detail: String(detail ?? '').slice(0, ARGS_LIMIT),
  }
}

/** 完成态的声明式卡片 → 若干行（PTC 的子操作就是这里的多行）。 */
export function presentResultLines(tools: any, name: string, args: unknown, result: unknown, fallback: string, scope?: unknown): string[] {
  let view: any
  try { view = definitionFor(tools, name, scope)?.presentResult?.(args, result) } catch { view = undefined }
  const lines: string[] = []
  const push = (value: unknown) => {
    if (typeof value !== 'string') return
    const flat = value.replace(/\s+/g, ' ').trim()
    if (flat) lines.push(flat.slice(0, SUMMARY_LIMIT))
  }
  if (view && typeof view === 'object') {
    if (view.title) push(view.title)
    if (view.card === 'terminal') {
      push(view.output)
      if (view.exitCode !== undefined) push(`exit ${view.exitCode}`)
    } else if (view.card === 'diff') {
      for (const diff of view.diffs ?? []) push(diff?.path)
    } else if (Array.isArray(view.matches)) {
      for (const group of view.matches) push(group?.path)
    } else {
      for (const block of view.content ?? []) if (block?.type === 'text') push(block.text)
    }
  }
  if (lines.length === 0) push(fallback)
  return lines.slice(0, MAX_RESULT_LINES)
}

/**
 * 把一条持久会话事件翻成一帧（不认识的事件返回 null，桥接不猜）。
 * 一条事件可以产出多帧（assistant/message 里既有思考又有正文），所以允许返回数组。
 */
export function frameForEvent(event: any, tools?: unknown, scope?: unknown): BridgeFrame | BridgeFrame[] | null {
  const data = event?.data ?? {}
  switch (event?.type) {
    case 'user/message':
      // 只有真人输入才是「用户气泡」；注入的 system/plugin 上下文不桥接。
      return data.source?.kind === 'user' ? { k: 'user', text: textOf(data.content) } : null
    case 'step/start':
      return { k: 'step', turn: Number(data.turn ?? 0), step: Number(data.step ?? 0) }
    case 'assistant/message': {
      const content = data.message?.content ?? data.content
      const thinking = reasoningOf(content)
      const text = textOf(content)
      const frames: BridgeFrame[] = []
      if (thinking) frames.push({ k: 'think', text: thinking })
      if (text) frames.push({ k: 'say', text, ...(data.interrupted === true ? { interrupted: true } : {}) })
      return frames.length > 0 ? frames : null
    }
    case 'tool/call': {
      const callId = String(data.callId ?? '')
      const name = String(data.name ?? '')
      let parsed: unknown = data.arguments
      try { parsed = JSON.parse(String(data.arguments ?? '{}')) } catch { /* 参数不是 JSON：原样交给 presenter */ }
      // 结果帧只带 callId；presenter 需要的工具名与参数在这里先记住（快照与直播都按顺序到达）
      argsByCallId.set(callId, { name, args: parsed })
      if (argsByCallId.size > 200) argsByCallId.delete(String(argsByCallId.keys().next().value))
      return {
        k: 'tool',
        callId,
        name,
        args: String(data.arguments ?? '').slice(0, ARGS_LIMIT),
        ...presentCallOf(tools, name, parsed, scope),
      }
    }
    case 'tool/ptc-dispatch': {
      // PTC 复合调用里的一次子操作（编辑/读取/命令…）：DSH 自己就是一条一条行铺出来的，
      // 这里照它的口径发一对帧——子操作自己的声明式卡片（tool）+ 它自己的结果行（toolDone）。
      // 不带 parentCallId 的记录不属于任何复合调用，接了也只会凭空多出一行。
      const parentCallId = String(data.parentCallId ?? '')
      if (!parentCallId) return null
      parentsWithSubOps.add(parentCallId)
      if (parentsWithSubOps.size > 200) parentsWithSubOps.delete(String(parentsWithSubOps.values().next().value))
      const callId = String(data.subCallId ?? '')
      if (!callId) return null
      const name = String(data.name ?? '')
      let parsed: unknown = data.arguments
      try { parsed = JSON.parse(String(data.arguments ?? '{}')) } catch { /* 参数不是 JSON：原样交给 presenter */ }
      const lines = presentResultLines(
        tools,
        name,
        parsed,
        { content: Array.isArray(data.content) ? data.content : [], isError: data.isError === true },
        name,
        scope,
      )
      return [
        { k: 'tool', callId, name, args: String(data.arguments ?? '').slice(0, ARGS_LIMIT), ...presentCallOf(tools, name, parsed, scope) },
        { k: 'toolDone', callId, ok: data.isError !== true, summary: lines[0] ?? name, lines },
      ]
    }
    case 'tool/result': {
      const { callId, ok, summary } = toolResultOf(data)
      const block = Array.isArray(data?.message?.content) ? data.message.content[0] : undefined
      const known = argsByCallId.get(callId)
      const lines = parentsWithSubOps.has(callId) ? [] : presentResultLines(
        tools,
        known?.name ?? '',
        known?.args,
        {
          content: Array.isArray(block?.content) ? block.content : [],
          isError: block?.isError === true,
          ...(data?.meta === undefined ? {} : { meta: data.meta }),
        },
        summary,
        scope,
      )
      return { k: 'toolDone', callId, ok, summary: lines[0] ?? summary, lines }
    }
    case 'turn/end':
      return { k: 'turnEnd', reason: String(data.reason?.kind ?? data.reason ?? 'completed') }
    default:
      return null
  }
}

/** 把一条直播助手流帧翻成一帧：正文与思考都过桥（思考在页面上折叠成一行）。 */
export function frameForStreamFrame(frame: any): BridgeFrame | null {
  const chunk = frame?.chunk
  if (frame?.type !== 'chunk') return null
  if (chunk?.type === 'text-delta') {
    const text = typeof chunk.text === 'string' ? chunk.text : ''
    return text ? { k: 'delta', text, live: true } : null
  }
  if (chunk?.type === 'reasoning-delta') {
    const text = typeof chunk.text === 'string' ? chunk.text : ''
    return text ? { k: 'think', text, live: true } : null
  }
  return null
}

/** 快照与追加共用一套翻译：Copree 侧只需一个 reducer。 */
function framesOf(records: unknown, tools?: unknown, scope?: unknown): BridgeFrame[] {
  if (!Array.isArray(records)) return []
  const out: BridgeFrame[] = []
  for (const record of records) {
    const frame = frameForEvent((record as any)?.event, tools, scope)
    if (Array.isArray(frame)) out.push(...frame)
    else if (frame) out.push(frame)
  }
  return out
}

/**
 * 同意接入的控制端点：只有 DSH 自己的设置页会调它。
 * 刻意与 /copree-bridge 不共享前缀——同意之前 /copree-bridge 整条前缀都不该存在，
 * 否则「未同意也留一个入口」就等于闸门只做了半扇。
 */
export const CONSENT_PATH = '/copree-consent'

/** 同意状态落本机文件：DSH 重启后仍保持人的决定。 */
function consentFile(): string {
  return join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'dsh-copree-consent.json')
}

/** 人有没有在 DSH 里点过「同意接入」；读不到或坏掉一律当没同意（安全默认）。 */
export function readConsent(): boolean {
  try {
    return JSON.parse(readFileSync(consentFile(), 'utf8'))?.copree === true
  } catch {
    return false
  }
}

/** 记下人的决定；写不了就抛错，让设置页如实报失败，而不是假装同意成功。 */
export function writeConsent(allowed: boolean): void {
  const file = consentFile()
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify({ copree: allowed, at: Date.now() }, null, 2), { mode: 0o600 })
}

/** 同意端点的分发：GET 读状态，POST 落决定并立刻生效。 */
function handleConsent(req: IncomingMessage, res: ServerResponse, apply: (allowed: boolean) => void): void {
  const route = new URL(req.url ?? '/', 'http://dsh.local').pathname
  if (route !== CONSENT_PATH && route !== `${CONSENT_PATH}/`) {
    sendJson(res, 404, { error: 'not found' })
    return
  }
  if (req.method === 'GET') {
    sendJson(res, 200, { allowed: readConsent() })
    return
  }
  if (req.method !== 'POST') {
    sendJson(res, 405, { error: 'method not allowed' })
    return
  }
  readJsonBody(req)
    .then((body) => {
      const allowed = (body as any)?.allowed === true
      try {
        writeConsent(allowed)
      } catch (error) {
        sendJson(res, 500, { error: String((error as Error)?.message ?? error) })
        return
      }
      apply(allowed)
      sendJson(res, 200, { allowed })
    })
    .catch((error) => sendJson(res, 400, { error: String((error as Error)?.message ?? error) }))
}

/** 桥接选项：全部来自插件配置，不接受请求方输入。 */
export type BridgeOptions = {
  /** 总开关（config.bridgeEnabled）。 */
  enabled: boolean
  /** 共享密钥（config.bridgeSecret）；为空则整条桥接不注册。 */
  secret: string
  /** 本插件对 Copree 侧宣告的可达地址（config.bridgeAdvertiseUrl）。 */
  advertiseUrl: string
  /** Copree 后端地址，用于心跳注册。 */
  backendUrl: string
  /** 插件版本，随注册带给 Copree 显示。 */
  version: string
  /** 心跳间隔（默认 HEARTBEAT_MS）；只为离线自检留的加速口。 */
  heartbeatMs?: number
  log: (message: string) => void
}

/** DSH Host 的 sessionController（只声明本模块用到的五个方法）。 */
type SessionController = {
  list(request: unknown, signal?: AbortSignal): Promise<{ items: any[] }>
  create(request: { sessionId?: string; cwd?: string }): Promise<{ sessionId: string }>
  prompt(request: { sessionId: string; content: unknown[]; requestId: string; mode?: 'steer' }, signal?: AbortSignal): Promise<{ accepted: boolean }>
  follow(request: unknown, signal: AbortSignal): AsyncIterable<any>
  cancel(request: { sessionId: string }): Promise<unknown>
}

/** 会话的工作区：附件落盘要落在 AI 读得到的地方；查不到就交回给调用方的兜底值。 */
async function sessionCwd(controller: SessionController, sessionId: string, fallback: string): Promise<string> {
  try {
    const items = (await controller.list({})).items
    const hit = items.find((item: any) => String(item?.sessionId) === sessionId)
    return String(hit?.cwd ?? '') || fallback
  } catch {
    return fallback
  }
}

/** 会话摘要：只带 Copree 需要展示的字段，projections 等内部结构不外传。 */
function sessionSummary(item: any): Record<string, unknown> {
  // 标题是 DSH 的 title 投影（session/title 事件折叠出的字符串），摘要顶层没有这个字段；
  // updatedAt 保持 DSH 的毫秒量纲（会话事件 time 就是 ms），前端按同一量纲算「几天前」。
  const projected = item?.projections?.values?.title
  return {
    sessionId: String(item?.sessionId ?? ''),
    title: typeof projected === 'string' ? projected : String(item?.title ?? ''),
    cwd: String(item?.cwd ?? ''),
    updatedAt: Number(item?.updatedAt ?? 0),
    running: item?.running === true,
  }
}

/** 恒定时间比较，避免用长度/前缀差异试探密钥。 */
function authorized(req: IncomingMessage, expected: string): boolean {
  const got = req.headers['x-copree-bridge-token']
  if (typeof got !== 'string' || got.length !== expected.length) return false
  return timingSafeEqual(Buffer.from(got), Buffer.from(expected))
}

/** 路由分发：所有分支先过鉴权，再按需读 body。 */
function handle(req: IncomingMessage, res: ServerResponse, controller: SessionController, options: BridgeOptions, tools: unknown, scopeOf: (sessionId: string) => unknown): void {
  const route = new URL(req.url ?? '/', 'http://dsh.local').pathname.slice(BRIDGE_PREFIX.length) || '/'
  if (!authorized(req, options.secret)) {
    sendJson(res, 401, { error: 'unauthorized' })
    return
  }
  const query = new URL(req.url ?? '/', 'http://dsh.local').searchParams
  const run = (fn: () => Promise<{ status?: number; body: unknown }>): void => {
    fn().then(
      ({ status = 200, body }) => sendJson(res, status, body),
      (error: unknown) => sendJson(res, 500, { error: String((error as Error)?.message ?? error) }),
    )
  }

  if (req.method === 'GET' && route === '/status') {
    // streams 是诊断字段：没人在看时提问/审批不会过桥（回落 DSH 默认链），
    // 页面说「没看到弹窗」时先看这里，能立刻分辨「没人订阅」还是「前端没渲染」
    sendJson(res, 200, { ok: true, plugin: 'dsh-copree', version: options.version, streams: askBroker.watchers() })
    return
  }
  if (req.method === 'GET' && route === '/sessions') {
    run(async () => ({ body: { items: (await controller.list({})).items.map(sessionSummary) } }))
    return
  }
  if (req.method === 'POST' && route === '/prompt') {
    run(async () => {
      const body = await readJsonBody(req, PROMPT_BODY_LIMIT)
      const { text: cleaned, images } = splitInlineImages(String(body.text ?? ''))
      if (!cleaned.trim() && images.length === 0) return { status: 400, body: { error: 'text is required' } }
      // 没有 sessionId 就现建一个：Copree 侧点「新对话」时不必先知道 DSH 的会话 id。
      const sessionId = body.sessionId
        ? String(body.sessionId)
        : (await controller.create(body.cwd ? { cwd: String(body.cwd) } : {})).sessionId
      // 图片先落盘、再把路径拼回正文：会话里的 AI 用自己的看图工具读文件，
      // 桥接依旧只搬文本，DSH 的会话记录里也不会留一坨 base64。
      const refs = Array.isArray(body.attachments) ? (body.attachments as AttachmentRef[]) : []
      let text = cleaned
      if (images.length > 0 || refs.length > 0) {
        const cwd = await sessionCwd(controller, sessionId, body.cwd ? String(body.cwd) : '')
        // 两条来源都留：老前端塞围栏、新前端只送引用；落盘后正文只有路径
        const inline = images.length > 0 ? await saveInlineImages(images, cwd) : { saved: [], skipped: 0 }
        const pulled = refs.length > 0 ? await pullAttachments(refs, cwd, options) : { saved: [], skipped: 0 }
        const saved = [...inline.saved, ...pulled.saved]
        const skipped = inline.skipped + pulled.skipped
        if (saved.length > 0) {
          text = [text, `[图片附件]\n${saved.map((p) => `- ${p}`).join('\n')}`].filter(Boolean).join('\n\n')
        }
        if (skipped > 0) {
          text = [text, `[图片附件] 有 ${skipped} 张未能保存（超出大小或数量上限）`].filter(Boolean).join('\n\n')
        }
      }
      // mode='steer'：DSH 原生语义是「插到最近一个步骤边界」——正在跑工具时消息当场
      // 进它的下一步，空闲时等同开新一轮（见 dsh-agent 的 steer 文档），前端因此不必自己排队。
      const mode = body.mode === 'steer' ? ('steer' as const) : undefined
      // DSH 的 prompt(request, signal) 会对 signal 立即 throwIfAborted：必须给一个
      // （本请求不接受取消，所以给一个永不过期的新 signal，语义就是「跑完为止」）。
      await controller.prompt(
        { sessionId, content: [{ type: 'text', text }], requestId: randomUUID(), ...(mode === undefined ? {} : { mode }) },
        new AbortController().signal,
      )
      return { body: { sessionId } }
    })
    return
  }
  if (req.method === 'POST' && route === '/cancel') {
    run(async () => {
      const body = await readJsonBody(req)
      await controller.cancel({ sessionId: String(body.sessionId ?? '') })
      return { body: { ok: true } }
    })
    return
  }
  if (req.method === 'POST' && route === '/answer') {
    run(async () => {
      const body = await readJsonBody(req)
      const id = String(body.id ?? '')
      if (!id) return { status: 400, body: { error: 'id is required' } }
      const decision: AskDecision = {}
      if (body.approve !== undefined) decision.approve = body.approve === true
      if (Array.isArray(body.answers)) {
        decision.answers = body.answers.map((a: any) => ({
          id: String(a?.id ?? ''),
          selected: Array.isArray(a?.selected) ? a.selected.map((x: any) => String(x)) : [],
          ...(a?.custom ? { custom: String(a.custom) } : {}),
        }))
      }
      if (!askBroker.answer(id, decision)) return { status: 404, body: { error: 'ask not found' } }
      return { body: { ok: true } }
    })
    return
  }
  if (req.method === 'GET' && route === '/stream') {
    const sessionId = query.get('sessionId') ?? ''
    if (!sessionId) { sendJson(res, 400, { error: 'sessionId is required' }); return }
    void streamSession(req, res, controller, sessionId, Number(query.get('afterSeq') ?? -1), tools, scopeOf)
    return
  }
  sendJson(res, 404, { error: 'not found' })
}

/**
 * SSE 回放一个会话：先补一份快照，再逐帧跟随。客户端断开即取消跟随，
 * 不留下悬挂的 follower（DSH 侧每帧都对照持久 seq 校验，重连安全）。
 */
async function streamSession(
  req: IncomingMessage,
  res: ServerResponse,
  controller: SessionController,
  sessionId: string,
  afterSeq: number,
  tools: unknown,
  scopeOf: (sessionId: string) => unknown,
): Promise<void> {
  // agent 平面的工具只在它的 scope 里可见：presentation 必须按这个会话的 agent 查
  const scope = scopeOf(sessionId)
  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache, no-transform',
    connection: 'keep-alive',
    'x-accel-buffering': 'no',
  })
  const write = (frame: BridgeFrame): void => { res.write(`data: ${JSON.stringify(frame)}\n\n`) }
  const abort = new AbortController()
  // 有人在看这条会话 = 它才有"等人回答"的能力（没人在看时请求直接交回 DSH 默认链）
  const unsubscribeAsks = askBroker.subscribe(sessionId, write)
  req.on('close', () => abort.abort())
  // 心跳注释行：让中间的 nginx / fetch 连接不至于因静默被掐断。
  const ping = setInterval(() => { res.write(': ping\n\n') }, HEARTBEAT_MS)
  ping.unref?.()
  try {
    const request = {
      address: { kind: 'session', sessionId },
      assistantStream: true,
      ...(Number.isSafeInteger(afterSeq) && afterSeq >= 0 ? { afterSeq } : {}),
    }
    for await (const item of controller.follow(request, abort.signal)) {
      if (item?.type === 'snapshot') {
        write({ k: 'snapshot', records: framesOf(item.records, tools, scope) })
        continue
      }
      if (item?.type === 'assistant-stream') {
        const frame = frameForStreamFrame(item.frame)
        if (frame) write(frame)
        continue
      }
      const frame = frameForEvent(item?.event, tools, scope)
      if (Array.isArray(frame)) frame.forEach(write)
      else if (frame) write(frame)
    }
  } catch (error) {
    if (!abort.signal.aborted) write({ k: 'error', message: String((error as Error)?.message ?? error) })
  } finally {
    unsubscribeAsks()
    clearInterval(ping)
    res.end()
  }
}

/**
 * 心跳注册：让 Copree 知道「插件在跑」「该往哪发请求」，并取回「是否已在 Copree 同意接入」。
 *
 * 同意是**在 Copree 管理端点**的：心跳回执里的 approved 才是准，DSH 侧不自己拍板。
 */
function startHeartbeat(options: BridgeOptions): () => void {
  if (!options.advertiseUrl) {
    options.log('dsh-copree: 反向桥接已启用但未注册（未配置 bridgeAdvertiseUrl）')
    return () => {}
  }
  let reported: boolean | null = null
  const beat = async (): Promise<void> => {
    let ok = false
    try {
      const response = await fetch(`${options.backendUrl}/dsh-bridge/register`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-copree-bridge-token': options.secret },
        body: JSON.stringify({
          plugin: 'dsh-copree',
          version: options.version,
          advertiseUrl: options.advertiseUrl,
        }),
        signal: AbortSignal.timeout(5_000),
      })
      ok = response.ok
    } catch {
      ok = false
    }
    if (ok !== reported) {
      reported = ok
      options.log(ok
        ? `dsh-copree: 反向桥接已注册 -> ${options.advertiseUrl}`
        : 'dsh-copree: 反向桥接注册失败（Copree 后端不可达或密钥不匹配）')
    }
  }
  void beat()
  const timer = setInterval(() => { void beat() }, options.heartbeatMs ?? HEARTBEAT_MS)
  timer.unref?.()
  return () => clearInterval(timer)
}

/**
 * 注册反向桥接。返回 disposer（插件卸载时摘掉路由与心跳）。
 * @param ctx - harness context exposing webServer and sessionController.
 * @param options - bridge config resolved from the plugin config.
 * @returns dispose function for the route and the heartbeat timer.
 */
/**
 * dsh-scope 的 `scopeOf`：动态取（顶层 import 一旦解析不到会把整个插件拖死）。
 * 取到了，工具卡片才能按「这条会话的 agent scope」查到声明；取不到就退化成通用卡片。
 */
let scopeOfFn: ((ctx: unknown) => unknown) | undefined

async function initScopeSupport(log: (message: string) => void): Promise<void> {
  try {
    const mod: any = await import('@deepseek-ai/dsh-scope')
    if (typeof mod?.scopeOf === 'function') {
      scopeOfFn = mod.scopeOf
      return
    }
    log('dsh-copree: dsh-scope 没有导出 scopeOf（工具卡片将退化为通用卡片）')
  } catch (error) {
    log(`dsh-copree: 载入 dsh-scope 失败（工具卡片将退化为通用卡片）：${String((error as Error)?.message ?? error)}`)
  }
}

export function registerBridge(ctx: Context, options: BridgeOptions): () => void {
  if (!options.enabled) {
    options.log('dsh-copree: 反向桥接未启用（bridgeEnabled=false）')
    return () => {}
  }
  const controller = (ctx as any).sessionController as SessionController | undefined
  /**
   * 会话的 agent scope：agent 平面的工具（run_code/read/edit/bash…）只在它的 scope 里可见，
   * presentation 与 PTC 子操作都要按这个 scope 查；会话不活跃时拿不到，退回通用卡片。
   */
  let scopeState: 'ok' | 'missing' | null = null
  const scopeOf = (sessionId: string): unknown => {
    try {
      // dsh-scope 的 scope key 不是 agent.ctx 本身，而是挂在它上面的符号属性
      // （dsh-tools 自己也是 scopeOf(ctx) 这么取的）。拿不到就返回 undefined → 通用卡片。
      const agentCtx = (ctx as any).agents?.get?.(sessionId)?.ctx
      const scope = scopeOfFn ? scopeOfFn(agentCtx) : undefined
      // 只报一次状态变化：scope 拿不到时 presentation 会退化成通用卡片，
      // 这正是「标签只有工具名、子操作没有」的现场，必须能从日志一眼看出
      if (scope === undefined) {
        if (scopeState !== 'missing') { scopeState = 'missing'; options.log('dsh-copree: 桥接取不到会话的 agent scope（工具卡片将退化为通用卡片）') }
      } else if (scopeState !== 'ok') {
        scopeState = 'ok'
        options.log('dsh-copree: 桥接已拿到 agent scope（工具卡片按工具自己的声明渲染）')
      }
      return scope
    } catch { return undefined }
  }
  /**
   * 开门 / 关门：**人点了同意才有桥**。
   *
   * 为什么做成「按需注册路由 + 按需发心跳」，而不是一个 if 判断放行：没同意时 DSH 上不该
   * 存在任何桥接端点，也不该向 Copree 暴露自己的存在（连心跳都不发）——这才叫物理隔绝；
   * 否则 Copree 至少能看见"有台 DSH 在喊我"，闸门只做了半扇。
   */
  let disposeRoute: (() => void) | null = null
  let stopHeartbeat: (() => void) | null = null
  const apply = (allowed: boolean): void => {
    if (allowed === (disposeRoute !== null)) return
    if (allowed) {
      if (!options.secret) {
        options.log('dsh-copree: 已同意接入，但未配置 bridgeSecret，桥接无法启动')
        return
      }
      if (!controller) {
        options.log('dsh-copree: 已同意接入，但当前 profile 没有 sessionController，桥接无法启动')
        return
      }
      // 尽快把 scopeOf 拿到手（异步、失败不影响桥接本身）
      void initScopeSupport(options.log)
      disposeRoute = ctx.webServer.register({
        kind: 'prefix',
        path: BRIDGE_PREFIX,
        handler: (req, res) => handle(req, res, controller, options, (ctx as any).tools, scopeOf),
      })
      stopHeartbeat = startHeartbeat(options)
      options.log('dsh-copree: 已同意 Copree 接入，桥接路由与心跳已启动')
    } else {
      disposeRoute?.()
      disposeRoute = null
      stopHeartbeat?.()
      stopHeartbeat = null
      options.log('dsh-copree: 已撤销同意，桥接路由与心跳已停')
    }
  }
  // 回答者瀑布只在桥接启用时挂：没同意桥接的 DSH 不该多出两个监听
  const disposeAnswerers = registerAnswerers(ctx)
  // 控制端点永远在（否则设置页没法把闸门打开）；它只做一件事：开关上面那一套
  const disposeConsent = ctx.webServer.register({
    kind: 'prefix',
    path: CONSENT_PATH,
    handler: (req, res) => handleConsent(req, res, apply),
  })
  // 重启后按人上次的决定恢复（默认关：没点过就是没同意）
  apply(readConsent())
  return () => { disposeConsent(); disposeRoute?.(); stopHeartbeat?.(); disposeAnswerers() }
}
