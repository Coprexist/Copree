/**
 * DSH 对话页 —— 与「群视界对话 · 放大」（WorldDesignPage?focus=1）同一套版式与零件。
 *
 * 为什么这么排：DSH 的会话有上下文、工具调用与长输出，需要的是"会话列表在左、
 * 对话列占满其余"的完整界面，而不是一个下拉框 + 一个 textarea。所以左栏复用群视界
 * 放大版的会话列表形态，右侧复用同一份零件（components/shared/ChatPanelAtoms：
 * 内容列宽 + 工具条 + 拖条），输入区复用主站/群视界共用的附件 hook 与 chips。
 *
 * 与群视界对话的差别只在数据源：那边是 useWorldChat 的 WS 流，这边是 DSH 桥接的 SSE 帧；
 * 所以本页**不解释也不保存**会话内容，只把帧铺开。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowDown, Bot, Brain, CheckCircle2, ChevronDown, Circle, ImagePlus, Info, MessageSquare, Plus, RefreshCw, Send, Square, Terminal, XCircle } from 'lucide-react'
import { api, fileDownloadUrl, getApiBaseUrl } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { DropMask, AttachmentChips } from '../components/AttachmentChips'
import { IconButton, MENU_CAPTION, MenuItem, MenuPanel, WidthHandles } from '../components/ui'
import { ApprovalDialog, QuestionDialog, type AskPrompt, type ChatApproval } from '../components/shared/ChatDialogs'
import MarkdownContent from '../components/shared/MarkdownContent'
import { CONTENT_W_VAR, ToolBubble, toolIcon, useContentColumnWidth } from '../components/shared/ChatPanelAtoms'
import { useAttachmentUpload, attachmentImageSizing, type ReadyAttachment } from '../hooks/useAttachmentUpload'
import { useElementWidth } from '../hooks/useElementWidth'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { streamSse } from '../utils/sse'
import {
  imageMarker, imageRefsIn,
  stripImageMarkers, supportsImageAttachments, type DshAttachmentRef, type DshImageRef,
} from '../utils/dshAttachments'

interface Status {
  detected: boolean
  state: 'online' | 'unreachable' | 'offline'
  version?: string
  last_seen_seconds?: number | null
  secret_configured?: boolean
  detail?: string
}

interface SessionItem {
  sessionId: string
  title: string
  cwd: string
  /** DSH 的毫秒时间戳（会话事件 time 即 ms） */
  updatedAt: number
  running: boolean
}

/** 已交给 DSH、但还没在会话里回显的一条消息（先给气泡，避免用户以为没发出去而再按一次） */
interface OutboxItem { id: number; text: string }

/**
 * 一条命令的本地回执。
 * 命令不进模型、也不会作为消息回显，所以它**没有**"等回显"这个阶段：
 * POST 的返回就是它的结论，气泡一出现就已经结算（running 只是请求还没回来）。
 */
interface CommandReceipt { id: number; text: string; state: 'running' | 'ok' | 'error'; detail: string }

/** DSH 侧注册的一条斜杠命令（名字不带 /；hint 非空表示要自由输入） */
interface DshCommand { name: string; description: string; hint: string }

/** 命令写法：/名字 + 可选参数（与 DSH 的 parseCommand 同口径——名字是字母开头的标识符） */
const COMMAND_LINE = /^\/[A-Za-z][A-Za-z0-9_-]*(\s|$)/

/**
 * 忙时的发送行为（与 DSH 本体同一套策略，见 dsh-client-ui-conversation 的 resolveSubmitMode）：
 * Enter / 发送按钮用选中的那种，Ctrl(Cmd)+Enter 用另一种。
 * 两者都只是"把这条消息交出去"，区别只在 DSH 收到它的时机——
 *   queue：排在当前这一轮之后当下一轮（DSH 的默认）
 *   steer：插到最近的步骤边界，正在跑的 AI 当场读到
 * 空闲时没有区别：都是开新一轮。
 */
const BUSY_SEND_KEY = 'copree.dsh.busySend'
type BusySend = 'queue' | 'steer'

function readBusySend(): BusySend {
  try { return localStorage.getItem(BUSY_SEND_KEY) === 'steer' ? 'steer' : 'queue' } catch { return 'queue' }
}

interface Line {
  id: number
  role: 'user' | 'ai' | 'tool' | 'think' | 'phase'
  text: string
  live?: boolean
  ok?: boolean
  /** 用户消息里的图片标记解析结果（气泡里渲染缩略图） */
  images?: DshImageRef[]
  /** 工具行：DSH 工具自己声明的卡片（card/kind/title）+ 完整参数（展开里看） */
  name?: string
  detail?: string
  callId?: string
  running?: boolean
  card?: string
  /** DSH 的中立分类词表：read/edit/delete/move/search/execute/fetch/other */
  kind?: string
  title?: string
  /** 完成态卡片铺开的多行（PTC 的子操作就在这里） */
  lines?: string[]
}

/**
 * 子会话（裸 UUID，不带 session- 前缀）不是可跟随的地址：桥接跟它会直接报
 * 「subagent Sessions require their durable parent address」。列表里列出来只会
 * 让人点进去看到一句空——不如不收。
 */
function isViewableSession(sessionId: string): boolean {
  return sessionId.startsWith('session-')
}

/**
 * DSH 卡片词表 → i18n key 末段。这是 DSH 自己定义的**中立分类**
 * （dsh-tools 的 ToolCallKind：read/edit/delete/move/search/execute/fetch/other），
 * 不是按工具名特判 —— 标签文案由工具声明的 kind 决定，认不出就原样显示工具名。
 */
/**
 * DSH 声明的中立分类 → 界面文案。分类表是**唯一的**「认识哪些 kind」的地方：
 * 未列出的分类原样显示工具名（猜一个中文名反而会误导），列出来的必须是三语都有的 key。
 */
const KIND_LABEL_KEYS: Record<string, string> = {
  read: 'tool:dsh.kind.read',
  edit: 'tool:dsh.kind.edit',
  delete: 'tool:dsh.kind.delete',
  move: 'tool:dsh.kind.move',
  search: 'tool:dsh.kind.search',
  execute: 'tool:dsh.kind.execute',
  fetch: 'tool:dsh.kind.fetch',
  other: 'tool:dsh.kind.other',
  // 思考不是工具分类，但阶段标题与它共用同一张表
  __think: 'tool:dsh.kind.think',
}

/**
 * 「工具只声明了卡片、没声明分类」时按卡片补一个分类。
 * DSH 的 fs / bash 工具就是这么声明的：presentCall 给 card + title，不给 kind
 * （见 dsh-tool-fs 的 write/edit）。卡片是工具自己说的"这次产生了什么"——
 * diff 改了文件、terminal 跑了命令——照它补分类是读声明，不是猜名字。
 */
const KIND_BY_CARD: Record<string, string> = { diff: 'edit', terminal: 'execute' }

/** 归一化分类：真分类优先；只有 other/空 时才看卡片 */
function normalizeKind(kind: string, card: string): string {
  const declared = (kind || '').trim()
  if (declared && declared !== 'other') return declared
  return KIND_BY_CARD[(card || '').trim()] || declared || 'other'
}

/**
 * 卡片标签：只认 DSH 声明的中立分类（kind），不按工具名特判。
 * 拿不到分类（旧插件）就把工具名原样显示——猜一个中文名反而会误导。
 * 放在模块层是因为工具行也是 memo 组件：它得自己算标签，不能靠父组件现做一个回调。
 */
function kindLabel(t: ReturnType<typeof useT>, value?: string): string {
  const key = (value || '').trim()
  const labelKey = key ? KIND_LABEL_KEYS[key] : undefined
  if (labelKey) return t(labelKey)
  return key || t('tool:dsh.kind.other')
}

/**
 * 工具行摘要：DSH 自己的界面也只给一行短文，不是把参数倒出来。
 * 优先模型写的 description，其次常见的路径/命令字段，最后退回参数原文首行。
 */
function toolSummary(args: string): string {
  let parsed: any = null
  try { parsed = JSON.parse(args || '{}') } catch { /* 参数不是 JSON：直接看原文 */ }
  const pick = (value: unknown) => (typeof value === 'string' && value.trim() ? value.trim() : '')
  const first = [
    pick(parsed?.description), pick(parsed?.command), pick(parsed?.file_path), pick(parsed?.path),
    pick(parsed?.query), pick(parsed?.url), pick(parsed?.endpoint), pick(parsed?.pattern), pick(parsed?.code),
  ].find(Boolean)
  const line = (first || args || '').split('\n').map((s) => s.trim()).filter(Boolean)[0] || ''
  return line.replace(/\s+/g, ' ').slice(0, 120)
}

/** 按阶段分组：阶段行 + 其下的工具/思考；用户与正文各自成组（DSH 自己的界面就这么分） */
function groupLines(lines: Line[]): Array<{ phase: Line | null; items: Line[] }> {
  const out: Array<{ phase: Line | null; items: Line[] }> = []
  let current: { phase: Line | null; items: Line[] } = { phase: null, items: [] }
  const flush = () => { if (current.phase || current.items.length) out.push(current) }
  for (const line of lines) {
    if (line.role === 'phase') { flush(); current = { phase: line, items: [] }; continue }
    if (line.role === 'tool' || line.role === 'think') { current.items.push(line); continue }
    flush()
    out.push({ phase: null, items: [line] })
    current = { phase: null, items: [] }
  }
  flush()
  return out
}

/** 阶段行：进入新步骤先立一行，标题由这一步第一条工具/思考补上（对齐 DSH 自己的分组行） */
function withPhaseTitle(lines: Line[], name: string, summary: string): Line[] {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i]
    if (line.role === 'phase') {
      if (line.text || !summary) return lines
      const next = lines.slice()
      next[i] = { ...line, name, text: summary }
      return next
    }
    if (line.role === 'tool' || line.role === 'think') continue
    return lines
  }
  return lines
}

/** 工具完成：按 callId 配回它的调用行；配不上就单开一行（宁可多一行，不丢结果） */
function settleTool(lines: Line[], callId: string, ok: boolean, summary: string, nextId: () => number, resultLines?: string[]): Line[] {
  let index = -1
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i]
    if (line.role !== 'tool') continue
    if (callId && line.callId === callId) { index = i; break }
    if (!callId && line.running) { index = i; break }
  }
  if (index < 0) return lines.concat([{ id: nextId(), role: 'tool' as const, text: summary, ok, detail: summary }])
  const next = lines.slice()
  const current = next[index]
  next[index] = {
    ...current,
    running: false,
    ok,
    // 完成后这一行显示结果摘要（与 DSH 同款），参数与结果都留在展开里
    text: summary || current.text,
    detail: [current.detail, ...(resultLines ?? [])].filter(Boolean).join('\n\n'),
    ...(resultLines && resultLines.length > 0 ? { lines: resultLines } : {}),
  }
  return next
}

/**
 * 本分组内最近一条「还开着的直播行」——直播增量接在它后面，落库的完整文本覆盖它。
 *
 * 为什么不能只看最后一行：一轮里工具帧会插在正文中间（先说要做什么，再调工具），
 * 那时最后一行是工具行，落库的 say 就会另起一行，同一段话在页面上出现两遍。
 */
function lastLiveLine(lines: Line[], role: 'ai' | 'think'): number {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i]
    if (line.role === 'phase' || line.role === 'user') return -1
    if (line.role === role && line.live) return i
  }
  return -1
}

/** DSH 帧 → 行列表：快照与直播共用一个 reducer（顺序回放即状态）。 */
function reduce(lines: Line[], frame: any, nextId: () => number): Line[] {
  switch (frame && frame.k) {
    case 'snapshot':
      return (frame.records as any[]).reduce((acc, record) => reduce(acc, record, nextId), [] as Line[])
    case 'user': {
      const text = String(frame.text ?? '')
      const images = imageRefsIn(text)
      return lines.concat([{ id: nextId(), role: 'user' as const, text, ...(images.length ? { images } : {}) }])
    }
    case 'delta': {
      const index = lastLiveLine(lines, 'ai')
      if (index >= 0) {
        const next = lines.slice()
        next[index] = { ...next[index], text: next[index].text + frame.text }
        return next
      }
      return lines.concat([{ id: nextId(), role: 'ai' as const, text: frame.text, live: true }])
    }
    case 'say': {
      // 落库文本是权威值：覆盖那条还开着的直播行，而不是并排再来一条
      const index = lastLiveLine(lines, 'ai')
      if (index >= 0) {
        const next = lines.slice()
        next[index] = { id: next[index].id, role: 'ai' as const, text: frame.text }
        return next
      }
      return lines.concat([{ id: nextId(), role: 'ai' as const, text: frame.text }])
    }
    case 'step':
      // 新步骤 = 新分组（DSH 自己的界面也把一轮工具归成一段）
      return lines.concat([{ id: nextId(), role: 'phase' as const, text: '' }])
    case 'think': {
      const text = String(frame.text ?? '')
      const live = frame.live === true
      const index = lastLiveLine(lines, 'think')
      if (index >= 0) {
        const next = lines.slice()
        // 增量往后接；落库的完整文本直接覆盖（同一段思考的两种来源只留一份）
        next[index] = { ...next[index], text: live ? next[index].text + text : text, live }
        return next
      }
      const summary = text.replace(/\s+/g, ' ').trim().slice(0, 120)
      return withPhaseTitle(
        lines.concat([{ id: nextId(), role: 'think' as const, text, live }]),
        '__think',
        summary,
      )
    }
    case 'tool': {
      const name = String(frame.name || '')
      const args = String(frame.args || '')
      // 卡片来自工具自己的声明（card/kind/title/detail）；只有旧插件才退回本地摘要
      const declared = typeof frame.card === 'string' && typeof frame.title === 'string'
      const kind = declared ? normalizeKind(String(frame.kind || ''), String(frame.card)) : ''
      const summary = declared ? String(frame.title || '') : toolSummary(args)
      const line: Line = {
        id: nextId(), role: 'tool', name, text: summary,
        detail: String(frame.detail ?? args),
        callId: String(frame.callId || ''), running: true,
        ...(declared ? { card: String(frame.card), kind, title: String(frame.title) } : {}),
      }
      return withPhaseTitle(lines.concat([line]), declared ? kind : name, summary)
    }
    case 'toolDone':
      return settleTool(
        lines,
        String(frame.callId || ''),
        frame.ok === true,
        String(frame.summary || ''),
        nextId,
        Array.isArray(frame.lines) ? (frame.lines as string[]) : undefined,
      )
    case 'error':
      return lines.concat([{ id: nextId(), role: 'ai' as const, text: String(frame.message || '') }])
    default:
      return lines
  }
}

/**
 * 思考块：默认三行，点开看全文。
 * 之前是死截断 + title 悬停——悬停既看不全也看不方便，与群视界对话的手感也不一致。
 */
const ThinkBlock = memo(function ThinkBlock({ line }: { line: Line }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <div className="min-w-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={t(open ? 'tool:dsh.think.collapse' : 'tool:dsh.think.expand')}
        className="flex w-full items-start gap-1.5 text-left rounded hover:bg-elevated/30 transition-colors"
      >
        <Brain size={11} className="shrink-0 mt-1 text-textMuted/70" />
        <div className={`flex-1 min-w-0 text-3xs text-textMuted whitespace-pre-wrap break-words ${open ? 'max-h-64 overflow-y-auto' : 'line-clamp-3'}`}>{line.text}</div>
        <ChevronDown size={11} className={`shrink-0 mt-1 text-textMuted/70 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
    </div>
  )
})

/**
 * 用户气泡与 AI 气泡各自 memo：
 * 输入框的每一次按键都会让页面这一层重渲染，消息体不该跟着重画——
 * 尤其 AI 气泡里的 Markdown 与工具卡片，重画一次就是一次完整解析。
 */
const UserBubble = memo(function UserBubble({ line }: { line: Line }) {
  const t = useT()
  return (
    <div className="flex justify-end">
      <div className="world-msg max-w-[90%] min-w-0 rounded-control px-3 py-2 text-sm bg-primary-500/20 text-textPrimary">
        <div className="text-3xs text-textMuted mb-0.5">{t('tool:dsh.msg.me')}</div>
        {!!line.images?.length && (
          <div className="flex flex-wrap gap-1.5 mb-1">
            {line.images.map((img) => {
              const url = fileDownloadUrl(img.file_id)
              return (
                <a key={img.file_id} href={url} target="_blank" rel="noreferrer" title={img.name}>
                  <img src={url} alt={img.name} className={`max-h-40 max-w-full rounded border border-border/60 ${attachmentImageSizing(img)}`} />
                </a>
              )
            })}
          </div>
        )}
        <span className="whitespace-pre-wrap break-words">{stripImageMarkers(line.text)}</span>
      </div>
    </div>
  )
})

const AiBubble = memo(function AiBubble({ line }: { line: Line }) {
  const t = useT()
  return (
    <div className="flex justify-start">
      <div className="world-msg max-w-[90%] min-w-0 rounded-control px-3 py-2 text-sm bg-elevated/80 text-textSecondary">
        <div className="text-3xs text-textMuted mb-0.5">{t('tool:dsh.msg.ai')}</div>
        {line.text
          ? <MarkdownContent content={line.text} />
          : <span className="italic opacity-50">{t('tool:dsh.msg.thinking')}</span>}
      </div>
    </div>
  )
})

/** 工具行：与气泡同一套 memo——一百多次工具调用在一条会话里很常见，按键时不该重画它们 */
const ToolRow = memo(function ToolRow({ line }: { line: Line }) {
  const t = useT()
  return (
    <div className="space-y-0.5">
      <ToolBubble
        name={kindLabel(t, line.kind || line.name)}
        label={line.title || line.text || ''}
        detail={line.detail}
        running={line.running}
        error={line.ok === false}
        icon={toolIcon((line.kind || line.name || '') + ' ' + (line.text || ''))}
      />
      {/* 完成态卡片铺开的多行：PTC 下 run_code 的子操作（编辑/读取/命令）就在这里 */}
      {!!line.lines?.length && (
        <div className="pl-5 space-y-0.5">
          {line.lines.map((text, li) => (
            <div key={li} className="text-3xs text-textMuted truncate" title={text}>{text}</div>
          ))}
        </div>
      )}
    </div>
  )
})

/**
 * 本地气泡：自己这边产生、不进会话的两类东西——「已发出、等回显」的消息与命令回执。
 * 骨架一模一样，只差语气（等待 / 执行中 / 成功 / 失败），所以合成一处，别再各写一副。
 */
const LocalNote = memo(function LocalNote({ label, text, tone, detail }: {
  label: string; text: string; tone: 'pending' | 'running' | 'ok' | 'error'; detail?: string
}) {
  const skin = tone === 'error'
    ? 'bg-rose-500/10 text-textPrimary/90 border-rose-400/40'
    : tone === 'ok' ? 'bg-primary-500/20 text-textPrimary border-primary-400/30'
      : 'bg-primary-500/10 text-textPrimary/80 border-dashed border-primary-400/30'
  return (
    <div className="flex justify-end">
      <div className={`world-msg max-w-[90%] min-w-0 rounded-control px-3 py-2 text-sm border ${skin}`}>
        <div className="text-3xs text-textMuted mb-0.5">{label}</div>
        <span className="whitespace-pre-wrap break-words">{text}</span>
        {!!detail && (
          <div className={`mt-1 whitespace-pre-wrap break-words text-3xs ${tone === 'error' ? 'text-rose-400' : 'text-textSecondary'}`}>{detail}</div>
        )}
      </div>
    </div>
  )
})

/** 相对时间：一眼看出哪条是刚聊过的（会话标题普遍为空，时间比标题好认） */
function useAgoLabel() {
  const t = useT()
  return useCallback((updatedAt: number) => {
    // updatedAt 是 DSH 的毫秒时间戳，和 Date.now() 同一量纲；再除 1000 会算出「两千万天前」
    const minutes = Math.floor((Date.now() - updatedAt) / 60000)
    if (minutes < 1) return t('tool:dsh.ago.now')
    if (minutes < 60) return t('tool:dsh.ago.minutes', { n: String(minutes) })
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return t('tool:dsh.ago.hours', { n: String(hours) })
    return t('tool:dsh.ago.days', { n: String(Math.floor(hours / 24)) })
  }, [t])
}

/** cwd 的末段：会话列表一行放不下整条路径，末段才是人认得出的那截 */
function cwdTail(cwd: string): string {
  const parts = (cwd || '').split('/').filter(Boolean)
  return parts[parts.length - 1] || cwd || ''
}

export default function DshChatPage() {
  const t = useT()
  const navigate = useNavigate()
  const agoLabel = useAgoLabel()
  const [status, setStatus] = useState<Status | null>(null)
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [sessionId, setSessionId] = useState('')
  const [lines, setLines] = useState<Line[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  /**
   * 忙时要「排队」还是「插队」：由用户在输入框上选，记在本地。
   * 以前是"忙就一律 steer"——想等这一轮跑完再发就没别的路了。
   */
  const [busySend, setBusySend] = useState<BusySend>(readBusySend)
  /** 这一页进来时这一轮是否已经在跑：流只会告诉你"结束了"，不会告诉你"进来时就在跑" */
  const busySeeded = useRef('')
  const [error, setError] = useState('')
  /** 快照是否已到：用来把「正在载入」与「这条会话确实没内容」分开 */
  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready'>('idle')
  /** DSH 侧等人回答的请求：审批要许可、提问要信息，都必须弹到这一页上 */
  const [asks, setAsks] = useState<AskPrompt[]>([])
  /** 已发出、等回显的气泡：没有它，用户点完发送看不到任何动静，就会再按一次（实测就是这么出现重复消息的） */
  const [outbox, setOutbox] = useState<OutboxItem[]>([])
  /** 命令回执：与 outbox 分开——那条列表的出口是"会话回显"，命令永远等不到，混进去就成了一条挂在页面上的幽灵气泡 */
  const [receipts, setReceipts] = useState<CommandReceipt[]>([])
  /** 一句解释性提示（例如"这条已经在会话里了，跳过"）：只说明情况，不当错误 */
  const [notice, setNotice] = useState('')
  /** DSH 侧注册的斜杠命令：命令在 DSH 本体执行、不进模型（见后端 /admin/dsh/command） */
  const [commands, setCommands] = useState<DshCommand[]>([])
  const [cmdOpen, setCmdOpen] = useState(false)
  /**
   * 取不到命令的原因（桥接/插件原话）：菜单里要如实说。
   * 501 才是"DSH 没提供命令服务"（插件旧 / 未重启），404 是"这条会话不在活动列表"——
   * 桥接已经把这几种分开了，页面一律译成"插件版本旧"就是误诊。
   */
  const [cmdProblem, setCmdProblem] = useState('')
  const cmdMenuRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const outboxSeq = useRef(1)
  const receiptSeq = useRef(1)
  /** 最近提交过的文本：5 秒内同一条再提交一次，判为"上一次没反馈、又按了一下"，直接忽略 */
  const recentSubmits = useRef<{ text: string; at: number }[]>([])
  const nextId = useRef(1)
  const streamAbort = useRef<AbortController | null>(null)
  /** 刷新会话列表的入口（流这边只拿到 turnEnd，列表里的 running 要单独刷一下才不会一直闪） */
  const refreshSessions = useRef<() => void>(() => {})
  // 贴底跟随：与群视界对话**同一份实现**（阈值/多列表/断开跟随都在 useStickToBottom 里）
  const stick = useStickToBottom()

  // 内容列宽：与群视界对话同一份实现（变量挂在 display:contents 那层，拖拽只改变量）
  const [measureRef, columnWidth] = useElementWidth()
  const colHostRef = useRef<HTMLDivElement | null>(null)
  const setColumnHost = useCallback((el: HTMLDivElement | null) => { colHostRef.current = el }, [])
  const { contentWidth, dragging, ...handleProps } = useContentColumnWidth(columnWidth, colHostRef)

  // 附件：与主站聊天/群视界对话共用的 hook（点选、拖拽、Ctrl+V 都走它）
  const attachments = useAttachmentUpload({ imagesOnly: true })
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const imagesSupported = supportsImageAttachments(status?.version)

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await api.get<Status>('/admin/dsh/status'))
    } catch (e: any) {
      setStatus({ detected: false, state: 'offline', detail: e && e.message ? e.message : String(e) })
    }
  }, [])

  const loadSessions = useCallback(async () => {
    try {
      const data = await api.get<{ items: SessionItem[] }>('/admin/dsh/sessions')
      setSessions((data.items || []).filter((item) => isViewableSession(item.sessionId)))
    } catch (e: any) {
      setError(e && e.message ? e.message : String(e))
    }
  }, [])

  // 把刷新入口交给流那一侧：一轮结束时列表里的 running 该灭了（它不会自己知道）
  useEffect(() => { refreshSessions.current = () => { void loadSessions() } })

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => {
    const timer = setInterval(loadStatus, 5000)
    return () => clearInterval(timer)
  }, [loadStatus])
  useEffect(() => {
    if (status && status.state === 'online') loadSessions()
  }, [status && status.state, loadSessions])

  // 命令挂在会话的 agent 上：没有会话就问不出「有哪些命令」，所以跟着会话走
  useEffect(() => {
    setCmdOpen(false)
    if (!sessionId || status?.state !== 'online') { setCommands([]); setCmdProblem(''); return }
    let cancelled = false
    // 命令挂在会话的 agent 上，而 agent 是随流被拉活的：刚切过去就取，可能还没活跃。
    // 失败后隔一拍自愈一次——这就是全部重试，不做指数退避（它只在切会话的那一瞬间可能发生）
    const load = (attempt: number) => {
      api.get<{ items: DshCommand[] }>(`/admin/dsh/commands?sessionId=${encodeURIComponent(sessionId)}`)
        .then((data) => { if (!cancelled) { setCommands(data.items || []); setCmdProblem('') } })
        .catch((e: any) => {
          if (cancelled) return
          if (attempt === 0) { setTimeout(() => load(1), 1500); return }
          setCommands([])
          const detail = String(e?.detail || e?.message || '')
          setCmdProblem(e?.status === 501 || !detail ? t('tool:dsh.cmd.unavailable') : detail)
        })
    }
    load(0)
    return () => { cancelled = true }
  }, [sessionId, status?.state])

  // 浮层：点外面或按 Esc 就收（与消息菜单同一套手感）
  useEffect(() => {
    if (!cmdOpen) return
    const onDown = (event: MouseEvent) => {
      if (cmdMenuRef.current && !cmdMenuRef.current.contains(event.target as Node)) setCmdOpen(false)
    }
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setCmdOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [cmdOpen])

  // 选中会话即开流：先补快照（reducer 会清空重建），再逐帧跟随。
  useEffect(() => {
    streamAbort.current && streamAbort.current.abort()
    setOutbox([])
    // 回执跟着会话走：换会话等于换一屏，上一屏的命令结论不该留在这里
    setReceipts([])
    if (!sessionId) { setPhase('idle'); return }
    const controller = new AbortController()
    streamAbort.current = controller
    setLines([])
    setBusy(false)
    setPhase('loading')
    // 换会话 = 重新从最新看起：复位「首次落底」，下次内容到达直接落底
    stick.resetFollow()
    // 开流即"一直在看"：断线由共享读帧器自愈重连，重连后 DSH 会补发快照与未答的提问/审批。
    // 为什么不能只开一次：DSH 的提问/审批是阻塞式的，流悄悄断掉页面就再也收不到请求，人会一直卡着。
    void streamSse({
      url: getApiBaseUrl() + '/admin/dsh/stream?sessionId=' + encodeURIComponent(sessionId),
      headers: { Authorization: 'Bearer ' + localStorage.getItem('access_token') },
      signal: controller.signal,
      onRetry: () => setNotice(t('tool:dsh.reconnect')),
      // 只有压根连不上才走到这里（未注册/未同意/鉴权失败）：说清原因，别留一个空白的对话页
      onError: (e: any) => setError(e && e.message ? e.message : String(e)),
      onFrame: (frame: any) => {
        // 除了快照与"这一轮结束"，任何一帧都说明这一轮还在跑：让按钮的「排队 / 插队」
        // 跟真实状态走，而不是等下一次 prompt 回来才更新
        if (frame && frame.k !== 'snapshot' && frame.k !== 'turnEnd' && frame.k !== 'error') setBusy(true)
        if (frame && frame.k === 'ask') {
          // 同一个请求可能被重连补发：按 id 去重，别弹出两个一样的窗
          setAsks((prev) => prev.some((a) => a.id === frame.ask.id) ? prev : [...prev, frame.ask])
          return
        }
        if (frame && frame.k === 'askDone') {
          setAsks((prev) => prev.filter((a) => a.id !== frame.id))
          return
        }
        setLines((prev) => reduce(frame && frame.k === 'snapshot' ? [] : prev, frame, () => nextId.current++))
        if (frame && frame.k === 'user') {
          const echoed = stripImageMarkers(String(frame.text ?? ''))
          setOutbox((prev) => {
            const i = prev.findIndex((o) => echoed.startsWith(o.text))
            if (i < 0) return prev
            const next = prev.slice()
            next.splice(i, 1)
            return next
          })
        }
        if (frame && frame.k === 'snapshot') { setPhase('ready'); setError('') }
        if (frame && frame.k === 'error') { setPhase('ready'); setError(String(frame.message || '')) }
        if (frame && (frame.k === 'turnEnd' || frame.k === 'error')) { setBusy(false); refreshSessions.current() }
      },
    })
    return () => controller.abort()
  }, [sessionId])

  // 新内容到达 → 在底部才跟随（判定/阈值/断开跟随都在共享 hook 里，这里只负责触发）
  useEffect(() => { stick.follow() }, [lines.length, stick.follow])

  /** 真正把一条消息投给 DSH（正文带图片标记；图片只送引用，字节由插件按 id 回取） */
  const sendNow = useCallback(async (text: string, ready: ReadyAttachment[], mode?: 'steer') => {
    setBusy(true)
    setError('')
    setOutbox((prev) => [...prev, { id: outboxSeq.current++, text }])
    try {
      // 正文只带「人也能读懂」的标记；附件另给一份引用，避免 base64 把请求撑爆链路上的代理
      const marks = ready.map((att) => imageMarker(att.file_id, att.name))
      const body = [text, ...marks].filter(Boolean).join('\n\n')
      const attachments: DshAttachmentRef[] | undefined = ready.length > 0 && imagesSupported
        ? ready.map((att) => ({ fileId: att.file_id, name: att.name, mime: att.mime_type }))
        : undefined
      // cwd 一起带上：图片要落到这条会话自己的工作区，插件不必再反查
      const res = await api.post<{ sessionId: string }>('/admin/dsh/prompt', {
        text: body,
        sessionId: sessionId || undefined,
        cwd: sessions.find((s) => s.sessionId === sessionId)?.cwd || undefined,
        attachments,
        mode,
      })
      if (!sessionId && res && res.sessionId) setSessionId(res.sessionId)
      loadSessions()
    } catch (e: any) {
      setBusy(false)
      setError(e && e.message ? e.message : String(e))
    }
  }, [imagesSupported, sessionId, sessions, loadSessions])

  /**
   * 执行一条斜杠命令：与发消息分开——命令不进模型，由 DSH 的 commands 服务直接跑。
   * 命令不会在会话里回显（它不是消息），所以这里自己记一条**回执**：
   * 结论（例如"已压缩 N 段历史"）就落在气泡里，而不是丢给只活 8 秒的提示——
   * 那样飘走之后，页面就只剩一条永远等不到回显的气泡，等于什么也没说。
   */
  const runCommand = useCallback(async (line: string) => {
    if (!sessionId) { setError(t('tool:dsh.cmd.needsSession')); return }
    setCmdOpen(false)
    setError('')
    const id = receiptSeq.current++
    setReceipts((prev) => [...prev, { id, text: line, state: 'running', detail: '' }])
    /** 结论只写一次：请求回来是唯一出口，异常也走这里，别让回执卡在"执行中" */
    const settle = (state: 'ok' | 'error', detail: string) => setReceipts((prev) => {
      const next = prev.map((item) => (item.id === id ? { ...item, state, detail } : item))
      // 回执只回答"我刚才做了什么"：留最近一屏，别让常开的页面一直长
      return next.length > 20 ? next.slice(next.length - 20) : next
    })
    try {
      const res = await api.post<{ result: 'success' | 'error'; text: string }>('/admin/dsh/command', { sessionId, line })
      if (res.result === 'error') settle('error', res.text || t('tool:dsh.cmd.failed'))
      else settle('ok', res.text || t('tool:dsh.cmd.done'))
    } catch (e: any) {
      settle('error', e && e.message ? e.message : String(e))
    }
  }, [sessionId, t])

  /** 菜单里选中一条命令：要参数的填进输入框（补完按 Enter 走同一条执行链），不要参数的直接跑 */
  const pickCommand = useCallback((cmd: DshCommand) => {
    if (cmd.hint) {
      setCmdOpen(false)
      setInput(`/${cmd.name} `)
      setNotice(t('tool:dsh.cmd.args'))
      requestAnimationFrame(() => {
        const el = inputRef.current
        if (!el) return
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      })
      return
    }
    void runCommand(`/${cmd.name}`)
  }, [runCommand, t])

  /**
   * 发送：命令走命令链（不进模型），消息按调用方给的时机交给 DSH（排队 / 插队，见 modeFor）。
   * 为什么不能"忙就丢掉"：丢掉时页面上看不出任何区别，用户以为发了——
   * 结果 DSH 没收到、他却已经在等回复。先记一条 outbox 气泡，让"还没回显"这件事可见。
   */
  const submit = (mode?: 'steer') => {
    const text = input.trim()
    const ready = attachments.ready
    if (!text && ready.length === 0) return
    // 命令写法 → 交给 DSH 的命令链。当消息发出去等于没执行：模型只会读到这行字，命令本身不会跑
    if (ready.length === 0 && COMMAND_LINE.test(text)) {
      setInput('')
      void runCommand(text)
      return
    }
    const now = Date.now()
    // 同一段文本在 5 秒内被提交两次：几乎都是"上一次没看到反馈、又按了一下"，
    // 放过去只会让 DSH 收到两条一模一样的消息（实测出现过，用户看到后说"我没发这个"）
    if (ready.length === 0 && recentSubmits.current.some((p) => p.text === text && now - p.at < 5000)) return
    recentSubmits.current = [...recentSubmits.current.filter((p) => now - p.at < 60_000), { text, at: now }]
    // 时机由调用方定（排队 / 插队，见 modeFor）：客户端不自己排队——
    // 排队要么等整轮跑完、要么在服务端再排一次，都是同一件事做两遍。
    void sendNow(text, ready, mode)
    setInput('')
    attachments.clear()
  }

  // 提示只活 8 秒：它是解释，不是状态
  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(''), 8000)
    return () => clearTimeout(timer)
  }, [notice])

  /** 回答 DSH 的问题/审批：交回桥接，然后把它从弹窗队列里摘掉 */
  const answerAsk = async (ask: AskPrompt, payload: Record<string, unknown>) => {
    try {
      await api.post('/admin/dsh/answer', { id: ask.id, ...payload })
      setAsks((prev) => prev.filter((a) => a.id !== ask.id))
    } catch (e: any) {
      setError(e && e.message ? e.message : String(e))
    }
  }

  const stop = async () => {
    if (!sessionId) return
    try { await api.post('/admin/dsh/cancel', { sessionId }) } catch { /* 停不下来就等它自己结束 */ }
    setBusy(false)
  }

  /**
   * 进来就对齐真实状态：会话列表里的 running 是 DSH 的投影，而流里只有 turnEnd
   * （"结束了"），没有"进门时已经在跑"这一帧。少了这一步，正在跑的会话会被当成空闲——
   * 按钮从「插队发送」变回「发送」，人以为它停了，再点一次就成了排队的下一轮。
   */
  useEffect(() => {
    if (!sessionId || busySeeded.current === sessionId) return
    const item = sessions.find((s) => s.sessionId === sessionId)
    if (!item) return
    busySeeded.current = sessionId
    setBusy(item.running)
  }, [sessions, sessionId])

  const chooseBusySend = (next: BusySend) => {
    setBusySend(next)
    try { localStorage.setItem(BUSY_SEND_KEY, next) } catch { /* 隐私模式下记不住就只本次生效 */ }
  }

  /**
   * 这一次交出去该用哪个时机：忙时按选的那种，另一个手势取反（与 DSH 的 Cmd/Ctrl+Enter 同为反选）；
   * 空闲时 undefined = 普通 prompt，就是开新一轮。
   */
  const modeFor = (gesture: 'enter' | 'other'): 'steer' | undefined => {
    if (!busy) return undefined
    const enterIsSteer = busySend === 'steer'
    return (gesture === 'enter' ? enterIsSteer : !enterIsSteer) ? 'steer' : undefined
  }

  const current = useMemo(() => sessions.find((item) => item.sessionId === sessionId), [sessions, sessionId])
  // 一条会话一个分组序列：阶段行 + 该段的工具/思考，渲染不再逐行平铺
  const groups = useMemo(() => groupLines(lines), [lines])
  const stateIcon = status && status.state === 'online'
    ? <CheckCircle2 size={12} className="text-mint-400" />
    : status && status.state === 'unreachable'
      ? <XCircle size={12} className="text-rose-400" />
      : <Circle size={12} className="text-textMuted" />
  const stateLabel = status && status.state === 'online'
    ? t('tool:dsh.state.online')
    : status && status.state === 'unreachable'
      ? t('tool:dsh.state.unreachable')
      : t('tool:dsh.state.offline')

  const pickSession = (id: string) => {
    setSessionId(id)
    setError('')
  }

  return (
    <div className="h-full flex bg-canvas text-textPrimary" {...attachments.zoneProps('page')}>
      <DropMask {...attachments.dropState('page')} label={t('tool:dsh.attach.drop')} />

      {/* ═══ 左栏：DSH 会话（放大后的群视界对话同构——会话在左，对话占满其余） ═══ */}
      <aside className="hidden md:flex w-60 shrink-0 flex-col border-r border-border bg-surface">
        <div className="flex items-center gap-1.5 px-3 h-10 border-b border-border shrink-0">
          <MessageSquare size={13} className="text-textMuted shrink-0" />
          <span className="text-xs font-medium text-textSecondary flex-1 truncate">{t('tool:dsh.sessions')}</span>
          <span className="text-3xs text-textMuted shrink-0">{sessions.length}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto scroll-floor py-1">
          {sessions.length === 0 && (
            <p className="px-3 py-2 text-xs text-textMuted">{t('tool:dsh.noSessions')}</p>
          )}
          {sessions.map((item) => (
            <button
              key={item.sessionId}
              onClick={() => pickSession(item.sessionId)}
              title={item.sessionId + ' · ' + item.cwd}
              className={`w-full text-left px-3 py-2 transition-colors ${
                item.sessionId === sessionId ? 'bg-primary-500/15 text-textPrimary' : 'text-textSecondary hover:bg-elevated'
              }`}
            >
              <span className="flex items-center gap-1.5">
                {item.running && <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-mint-400 animate-pulse" />}
                <span className="flex-1 min-w-0 truncate text-xs">{item.title || item.sessionId.slice(0, 18)}</span>
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-3xs text-textMuted">
                <span className="min-w-0 truncate">{cwdTail(item.cwd)}</span>
                <span className="shrink-0">·</span>
                <span className="shrink-0">{agoLabel(item.updatedAt)}</span>
              </span>
            </button>
          ))}
        </div>
        <button
          onClick={() => { setSessionId(''); setLines([]); setError(''); attachments.clear() }}
          className="shrink-0 m-2 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-control text-xs bg-primary-500/10 text-primary-400 hover:bg-primary-500/20 border border-primary-400/20 transition-colors"
        >
          <Plus size={12} /> {t('tool:dsh.newSession')}
        </button>
      </aside>

      {/* 对话列自己铺白底：页根是 bg-canvas（#f8fafc），列不给底色就会露出那层灰，
          而群视界放大版里这一列就是 bg-surface —— 两处必须同一层白 */}
      <section className="flex-1 min-w-0 flex flex-col bg-surface">
        {/* 顶栏：与群视界对话一致的高度与分隔线 */}
        <div className="flex items-center gap-2 px-3 h-10 border-b border-border shrink-0 bg-surface">
          <button onClick={() => navigate('/admin')} className="icon-btn-sm shrink-0" title={t('tool:dsh.page.back')} aria-label={t('tool:dsh.page.back')}>
            <ArrowLeft size={14} />
          </button>
          <Bot size={15} className="text-primary-400 shrink-0" />
          <span className="text-sm font-medium truncate">{t('tool:dsh.page.title')}</span>
          {/* 窄屏没有左栏，会话用下拉选（同一份数据、同一个入口函数） */}
          <select
            value={sessionId}
            onChange={(event) => pickSession(event.target.value)}
            className="md:hidden min-w-0 flex-1 text-xs bg-elevated border border-border rounded-control px-2 py-1 text-textPrimary focus:outline-none focus:border-primary-400"
          >
            <option value="">{t('tool:dsh.page.pick')}</option>
            {sessions.map((item) => (
              <option key={item.sessionId} value={item.sessionId}>
                {(item.title || item.sessionId.slice(0, 8)) + ' — ' + cwdTail(item.cwd)}
              </option>
            ))}
          </select>
          <span className="hidden md:flex flex-1" />
          <span className="hidden sm:flex items-center gap-1 text-xs text-textSecondary shrink-0">
            {stateIcon}
            {stateLabel}
            {status && status.version ? ' · v' + status.version : ''}
          </span>
          <button onClick={() => { loadStatus(); loadSessions() }} className="icon-btn-sm shrink-0" title={t('tool:dsh.refresh')} aria-label={t('tool:dsh.refresh')}>
            <RefreshCw size={13} />
          </button>
        </div>

        {/* 会话元信息：与「放大后的群视界对话」同样的弱化一行 */}
        <div className="px-3 py-1 border-b border-border shrink-0 text-3xs text-textMuted truncate">
          {current ? current.sessionId + ' · ' + current.cwd : t('tool:dsh.page.hint')}
        </div>

        {/* 功能边界：这里是 DSH 的一面镜子，说清楚，免得把「没做」当成「坏了」 */}
        <div className="flex items-center gap-1 px-3 py-1 border-b border-border shrink-0 text-3xs text-textMuted">
          <Info size={11} className="shrink-0" />
          <span className="truncate" title={t('tool:dsh.limited')}>{t('tool:dsh.limited')}</span>
        </div>

        {/* 消息区：外层不滚动（挂拖拽蒙版），内层滚动；列宽居中 + 可拖（与群视界对话同一套） */}
        <div ref={measureRef} className="flex-1 min-h-0 relative">
          <DropMask {...attachments.dropState('list')} label={t('tool:dsh.attach.drop')} />
          <div ref={setColumnHost} className="contents" style={{ [CONTENT_W_VAR]: `${contentWidth}px` } as React.CSSProperties}>
            <div
              ref={stick.listRef}
              className="absolute inset-0 overflow-y-auto py-3 space-y-2 [scrollbar-gutter:stable]"
              style={{
                paddingLeft: `max(0px, calc((100% - var(${CONTENT_W_VAR})) / 2))`,
                paddingRight: `max(0px, calc((100% - var(${CONTENT_W_VAR})) / 2))`,
              }}
            >
              {lines.length === 0 && (
                <p className="text-xs text-textMuted text-center mt-8">
                  {!sessionId ? t('tool:dsh.page.pick')
                    : phase === 'loading' ? t('tool:dsh.page.loading')
                      : t('tool:dsh.page.empty')}
                </p>
              )}
              {groups.map((group, gi) => (
                <div key={gi} className="space-y-1">
                  {/* 阶段行（DSH 同款）：这一步在干什么，一眼一行 */}
                  {group.phase && (
                    <div className="flex items-center gap-1.5 text-2xs text-textMuted pt-1 min-w-0">
                      {group.phase.name === '__think' ? <Brain size={11} /> : <Terminal size={11} />}
                      <span className="font-medium shrink-0">{kindLabel(t, group.phase.name)}</span>
                      {group.phase.text && <span className="truncate">{group.phase.text}</span>}
                    </div>
                  )}
                  <div className={group.phase ? 'pl-3 border-l border-border/60 space-y-1' : 'space-y-1'}>
                  {group.items.map((line) => {
                if (line.role === 'tool') return <ToolRow key={line.id} line={line} />
                if (line.role === 'think') return <ThinkBlock key={line.id} line={line} />
                const isUser = line.role === 'user'
                return isUser
                  ? <UserBubble key={line.id} line={line} />
                  : <AiBubble key={line.id} line={line} />
              })}
                  </div>
                </div>
              ))}
              {receipts.map((item) => (
                <LocalNote
                  key={'receipt-' + item.id}
                  label={t(item.state === 'running' ? 'tool:dsh.msg.cmdRunning' : 'tool:dsh.msg.cmd')}
                  tone={item.state}
                  text={item.text}
                  detail={item.detail || undefined}
                />
              ))}
              {outbox.map((item) => (
                <LocalNote key={'outbox-' + item.id} label={t('tool:dsh.msg.sending')} tone="pending" text={item.text} />
              ))}
            </div>
            <WidthHandles varName={CONTENT_W_VAR} dragging={dragging} title={t('tool:dsh.colWidth')} {...handleProps} />
          </div>
          {/* 回到底部：不在底部才给入口（与群视界对话同款；在底部时它不出现） */}
          {!stick.isAtBottom && <button
            onClick={() => stick.scrollToBottom()}
            className="absolute right-3 bottom-3 z-overlay p-1.5 rounded-full bg-elevated border border-border text-textSecondary hover:text-textPrimary hover:bg-surface transition-colors"
            title={t('tool:dsh.scrollBottom')}
            aria-label={t('tool:dsh.scrollBottom')}
          >
            <ArrowDown size={13} />
          </button>}
        </div>

        {notice && <div className="px-4 py-1 text-xs text-textMuted shrink-0">{notice}</div>}
        {error && <div className="px-4 py-1 text-xs text-rose-400 shrink-0">{error}</div>}

        {/* 输入区：附件可点选/拖拽/粘贴（与群视界对话同一套），底部一条只放附件与发送 */}
        <div className="border-t border-border p-3 relative" {...attachments.zoneProps('input')} {...attachments.pasteProps}>
          <DropMask {...attachments.dropState('input')} label={t('tool:dsh.attach.drop')} />
          <AttachmentChips items={attachments.items} onRemove={attachments.remove} />
          {attachments.ready.length > 0 && !imagesSupported && (
            <p className="mb-2 text-3xs text-accent-400">{t('tool:dsh.attach.unsupported')}</p>
          )}
          <div
            className="mx-auto w-full rounded-2xl border border-border bg-elevated/40 transition-colors focus-within:border-primary-500/60 focus-within:bg-surface"
            style={{ maxWidth: `calc(var(${CONTENT_W_VAR}) + 32px)` }}
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => {
                setInput(event.target.value)
                const el = event.target
                el.style.height = 'auto'
                el.style.height = Math.min(el.scrollHeight, 240) + 'px'
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' || event.shiftKey) return
                event.preventDefault()
                // Ctrl/Cmd+Enter 走"另一种"时机（与 DSH 同款反选），忙时才有意义
                submit(modeFor(event.ctrlKey || event.metaKey ? 'other' : 'enter'))
              }}
              rows={2}
              placeholder={busy ? t('tool:dsh.input.busy') : t('tool:dsh.input')}
              className="w-full bg-transparent text-sm px-3 pt-2.5 pb-1 outline-none resize-none placeholder:text-textMuted"
            />
            <div className="flex items-center gap-1 px-2 pb-1.5">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/*"
                className="hidden"
                onChange={(event) => {
                  if (event.target.files?.length) attachments.pick(event.target.files)
                  event.target.value = ''
                }}
              />
              <IconButton size="sm" icon={<ImagePlus size={14} />} label={t('tool:dsh.input.attach')} onClick={() => fileInputRef.current?.click()} />
              {/* 斜杠命令：命令在 DSH 本体执行、不进模型，所以与「发消息」是两条链（按钮只是入口） */}
              <div className="relative" ref={cmdMenuRef}>
                <IconButton
                  size="sm"
                  icon={<Plus size={14} />}
                  label={t('tool:dsh.cmd.button')}
                  onClick={() => setCmdOpen((open) => !open)}
                />
                {cmdOpen && (
                  <MenuPanel className="absolute bottom-full left-0 mb-1 w-72 max-h-72 overflow-y-auto z-modal">
                    {commands.length === 0 ? (
                      // 三种"空"要分开说：没选会话 / 取不到命令服务 / 这条会话真没有命令
                      <div className={MENU_CAPTION}>
                        {!sessionId
                          ? t('tool:dsh.cmd.needsSession')
                          : cmdProblem || t('tool:dsh.cmd.empty')}
                      </div>
                    ) : commands.map((cmd) => (
                      <MenuItem key={cmd.name} onClick={() => pickCommand(cmd)} className="flex flex-col items-start gap-0.5">
                        <span className="font-medium">/{cmd.name}{cmd.hint ? ' …' : ''}</span>
                        {cmd.description && <span className="text-textMuted">{cmd.description}</span>}
                      </MenuItem>
                    ))}
                  </MenuPanel>
                )}
              </div>
              {busy && <span className="min-w-0 truncate text-3xs text-textMuted">{t('tool:dsh.input.busyHint')}</span>}
              <div className="flex-1" />
              {busy && <button onClick={stop} className="btn btn-sm btn-outline shrink-0"><Square size={12} /> {t('tool:dsh.stop')}</button>}
              {/* 忙时的两种时机：选中的那种给 Enter 和发送按钮，Ctrl/Cmd+Enter 走另一种 */}
              {busy && (
                <div role="group" aria-label={t('tool:dsh.busySend.title')} className="shrink-0 inline-flex items-center rounded-control border border-border overflow-hidden">
                  {(['queue', 'steer'] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => chooseBusySend(mode)}
                      aria-pressed={busySend === mode}
                      title={t(mode === 'queue' ? 'tool:dsh.busySend.queueHint' : 'tool:dsh.busySend.steerHint')}
                      className={`h-7 px-2 text-3xs transition-colors ${busySend === mode ? 'bg-primary-500/20 text-textPrimary' : 'text-textMuted hover:text-textPrimary hover:bg-elevated/40'}`}
                    >
                      {t(mode === 'queue' ? 'tool:dsh.busySend.queue' : 'tool:dsh.busySend.steer')}
                    </button>
                  ))}
                </div>
              )}
              <button
                onClick={() => submit(modeFor('enter'))}
                disabled={!input.trim() && attachments.ready.length === 0}
                className="shrink-0 inline-flex items-center gap-1 h-7 px-2.5 rounded-control bg-primary-500 hover:bg-primary-600 text-white text-xs font-medium transition-colors disabled:opacity-40"
              >
                {busy ? t(busySend === 'steer' ? 'tool:dsh.steer' : 'tool:dsh.send.queue') : t('tool:dsh.send')} <Send size={12} />
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* 等人回答：审批与提问都从同一条桥接帧来，弹在打开的这一页上。
          为什么必须在这里：DSH 的这两条链是阻塞的，没人答会话就停住，而人只会看到"AI 不动了" */}
      {asks.map((ask) => (
        ask.kind === 'approval' ? (
          <ApprovalDialog
            key={ask.id}
            approval={approvalOf(ask)}
            onDecide={(ok) => { void answerAsk(ask, { approve: ok }) }}
            allowNote={false}
            // DSH 的审批只有同意/不同意两档，没有"打字续时"的心跳，这里如实返回 0
            onTouch={async () => 0}
          />
        ) : (
          <QuestionDialog
            key={ask.id}
            ask={ask}
            onSubmit={(answers) => { void answerAsk(ask, { answers }) }}
          />
        )
      ))}
    </div>
  )
}

/** 桥接的审批请求 → 共享审批弹窗认的形状（DSH 只给工具名与理由，没有正文） */
function approvalOf(ask: AskPrompt): ChatApproval {
  return {
    approval_id: ask.id,
    kind: 'other',
    title: ask.toolName || '',
    detail: ask.reason || '',
  }
}
