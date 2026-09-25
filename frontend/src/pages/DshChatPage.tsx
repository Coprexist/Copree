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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowDown, Bot, Brain, CheckCircle2, Circle, ImagePlus, Info, MessageSquare, Plus, RefreshCw, Send, Square, Terminal, XCircle } from 'lucide-react'
import { api, getApiBaseUrl } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { DropMask, AttachmentChips } from '../components/AttachmentChips'
import { IconButton } from '../components/ui'
import { ApprovalDialog, QuestionDialog, type AskPrompt, type ChatApproval } from '../components/shared/ChatDialogs'
import MarkdownContent from '../components/shared/MarkdownContent'
import { CONTENT_W_VAR, ColumnWidthHandles, ToolBubble, toolIcon, useContentColumnWidth } from '../components/shared/ChatPanelAtoms'
import { useAttachmentUpload, type ReadyAttachment } from '../hooks/useAttachmentUpload'
import { useElementWidth } from '../hooks/useElementWidth'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { streamSse } from '../utils/sse'
import {
  attachmentUrl, imageMarker, imageRefsIn,
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
      const kind = declared ? String(frame.kind || 'other') : ''
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
  const [error, setError] = useState('')
  /** 快照是否已到：用来把「正在载入」与「这条会话确实没内容」分开 */
  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready'>('idle')
  /** DSH 侧等人回答的请求：审批要许可、提问要信息，都必须弹到这一页上 */
  const [asks, setAsks] = useState<AskPrompt[]>([])
  /** 已发出、等回显的气泡：没有它，用户点完发送看不到任何动静，就会再按一次（实测就是这么出现重复消息的） */
  const [outbox, setOutbox] = useState<OutboxItem[]>([])
  /** 一句解释性提示（例如"这条已经在会话里了，跳过"）：只说明情况，不当错误 */
  const [notice, setNotice] = useState('')
  const outboxSeq = useRef(1)
  /** 最近提交过的文本：5 秒内同一条再提交一次，判为"上一次没反馈、又按了一下"，直接忽略 */
  const recentSubmits = useRef<{ text: string; at: number }[]>([])
  const nextId = useRef(1)
  const streamAbort = useRef<AbortController | null>(null)
  // 贴底跟随：与群视界对话**同一份实现**（阈值/多列表/断开跟随都在 useStickToBottom 里）
  const stick = useStickToBottom()

  // 内容列宽：与群视界对话同一份实现（变量挂在 display:contents 那层，拖拽只改变量）
  const [measureRef, columnWidth] = useElementWidth()
  const colHostRef = useRef<HTMLDivElement | null>(null)
  const setColumnHost = useCallback((el: HTMLDivElement | null) => { colHostRef.current = el }, [])
  const { contentWidth, dragging, onHandleDown } = useContentColumnWidth(columnWidth, colHostRef)

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

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => {
    const timer = setInterval(loadStatus, 5000)
    return () => clearInterval(timer)
  }, [loadStatus])
  useEffect(() => {
    if (status && status.state === 'online') loadSessions()
  }, [status && status.state, loadSessions])

  // 选中会话即开流：先补快照（reducer 会清空重建），再逐帧跟随。
  useEffect(() => {
    streamAbort.current && streamAbort.current.abort()
    setOutbox([])
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
        if (frame && (frame.k === 'turnEnd' || frame.k === 'error')) setBusy(false)
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
   * 发送：空闲就直接发，正在跑就排进队列。
   * 为什么不能"忙就丢掉"：丢掉时页面上看不出任何区别，用户以为发了——
   * 结果 DSH 没收到、他却已经在等回复。排队面板让"还没发出去"这件事可见。
   */
  const submit = () => {
    const text = input.trim()
    const ready = attachments.ready
    if (!text && ready.length === 0) return
    const now = Date.now()
    // 同一段文本在 5 秒内被提交两次：几乎都是"上一次没看到反馈、又按了一下"，
    // 放过去只会让 DSH 收到两条一模一样的消息（实测出现过，用户看到后说"我没发这个"）
    if (ready.length === 0 && recentSubmits.current.some((p) => p.text === text && now - p.at < 5000)) return
    recentSubmits.current = [...recentSubmits.current.filter((p) => now - p.at < 60_000), { text, at: now }]
    // 忙就 steer：DSH 把消息插到最近一个步骤边界，AI 当场看到；闲就正常开一轮。
    // 客户端不再自己排队——排队要么等整轮跑完、要么在服务端再排一次，都是同一件事做两遍。
    void sendNow(text, ready, busy ? 'steer' : undefined)
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

  const current = useMemo(() => sessions.find((item) => item.sessionId === sessionId), [sessions, sessionId])
  // 一条会话一个分组序列：阶段行 + 该段的工具/思考，渲染不再逐行平铺
  const groups = useMemo(() => groupLines(lines), [lines])
  /**
   * 卡片标签：只认 DSH 声明的中立分类（kind），不按工具名特判。
   * 拿不到分类（旧插件）就把工具名原样显示——猜一个中文名反而会误导。
   */
  const toolLabel = useCallback((value?: string) => {
    const key = (value || '').trim()
    const labelKey = key ? KIND_LABEL_KEYS[key] : undefined
    if (labelKey) return t(labelKey)
    return key || t('tool:dsh.kind.other')
  }, [t])
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
        <div className="flex-1 min-h-0 overflow-y-auto py-1">
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
                      <span className="font-medium shrink-0">{toolLabel(group.phase.name)}</span>
                      {group.phase.text && <span className="truncate">{group.phase.text}</span>}
                    </div>
                  )}
                  <div className={group.phase ? 'pl-3 border-l border-border/60 space-y-1' : 'space-y-1'}>
                  {group.items.map((line) => {
                if (line.role === 'tool') {
                  return (
                    <div key={line.id} className="space-y-0.5">
                      <ToolBubble
                        name={toolLabel(line.kind || line.name)}
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
                }
                if (line.role === 'think') {
                  return (
                    <div key={line.id} className="text-3xs text-textMuted whitespace-pre-wrap line-clamp-3" title={line.text}>
                      {line.text}
                    </div>
                  )
                }
                const isUser = line.role === 'user'
                return (
                  <div key={line.id} className={isUser ? 'flex justify-end' : 'flex justify-start'}>
                    <div className={`world-msg max-w-[90%] min-w-0 rounded-control px-3 py-2 text-sm ${
                      isUser ? 'bg-primary-500/20 text-textPrimary' : 'bg-elevated/80 text-textSecondary'
                    }`}>
                      <div className="text-3xs text-textMuted mb-0.5">{isUser ? t('tool:dsh.msg.me') : t('tool:dsh.msg.ai')}</div>
                      {!!line.images?.length && (
                        <div className="flex flex-wrap gap-1.5 mb-1">
                          {line.images.map((img) => (
                            <a key={img.file_id} href={attachmentUrl(img.file_id)} target="_blank" rel="noreferrer" title={img.name}>
                              <img src={attachmentUrl(img.file_id)} alt={img.name} className="max-h-40 max-w-full rounded border border-border/60" />
                            </a>
                          ))}
                        </div>
                      )}
                      {isUser ? (
                        <span className="whitespace-pre-wrap break-words">{stripImageMarkers(line.text)}</span>
                      ) : (
                        line.text ? <MarkdownContent content={line.text} /> : <span className="italic opacity-50">{t('tool:dsh.msg.thinking')}</span>
                      )}
                    </div>
                  </div>
                )
              })}
                  </div>
                </div>
              ))}
              {outbox.map((item) => (
                <div key={'outbox-' + item.id} className="flex justify-end">
                  <div className="world-msg max-w-[90%] min-w-0 rounded-control px-3 py-2 text-sm bg-primary-500/10 text-textPrimary/80 border border-dashed border-primary-400/30">
                    <div className="text-3xs text-textMuted mb-0.5">{t('tool:dsh.msg.sending')}</div>
                    <span className="whitespace-pre-wrap break-words">{item.text}</span>
                  </div>
                </div>
              ))}
            </div>
            <ColumnWidthHandles dragging={dragging} onHandleDown={onHandleDown} title={t('tool:dsh.colWidth')} />
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
              value={input}
              onChange={(event) => {
                setInput(event.target.value)
                const el = event.target
                el.style.height = 'auto'
                el.style.height = Math.min(el.scrollHeight, 240) + 'px'
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() }
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
              {busy && <span className="min-w-0 truncate text-3xs text-textMuted">{t('tool:dsh.input.busyHint')}</span>}
              <div className="flex-1" />
              {busy && <button onClick={stop} className="btn btn-sm btn-outline shrink-0"><Square size={12} /> {t('tool:dsh.stop')}</button>}
              <button
                onClick={submit}
                disabled={!input.trim() && attachments.ready.length === 0}
                className="shrink-0 inline-flex items-center gap-1 h-7 px-2.5 rounded-control bg-primary-500 hover:bg-primary-600 text-white text-xs font-medium transition-colors disabled:opacity-40"
              >
                {busy ? t('tool:dsh.steer') : t('tool:dsh.send')} <Send size={12} />
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
