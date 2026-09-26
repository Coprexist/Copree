import { memo, useState, useRef, useCallback, useMemo, forwardRef, useImperativeHandle, useEffect } from 'react'
import { Send, Plus, ChevronRight, Brain, ArrowDown, ChevronDown, Copy, RefreshCw, ShieldAlert, HelpCircle } from 'lucide-react'
import MarkdownContent from './shared/MarkdownContent'
import CodeRenderer from './shared/CodeRenderer'
import { CONTENT_W_VAR, ToolBubble, toolIcon, useContentColumnWidth } from './shared/ChatPanelAtoms'
import { ApprovalDialog, PendingQueuePanel } from './shared/ChatDialogs'
import { IconButton, MenuPanel, MenuItem, INSET_CARD, confirmAsync } from './ui'
import { useWorldChat, type Approval, type ChatMsg } from '../hooks/useWorldChat'
import { useAttachmentUpload, isImageAttachment } from '../hooks/useAttachmentUpload'
import { AttachmentChips, DropMask } from './AttachmentChips'
import { api } from '../api/client'
import { useT } from '../i18n/I18nContext'
import { useElementWidth } from '../hooks/useElementWidth'
import ExpressionModeSwitch from './ExpressionModeSwitch'
import { copyText } from '../utils/clipboard'

// 运行模式三档（后端 world_ai_mode.MODES 是权威定义；这里只管展示与切换）
// 顺序 = 菜单从上到下：计划 / 自动 / 审阅（2026-09-18 用户：计划模式提到第一位）
const MODE_ITEMS = [
  { key: 'plan', labelKey: 'tool:world.mode.plan', hintKey: 'tool:world.mode.hint.plan' },
  { key: 'auto', labelKey: 'tool:world.mode.auto', hintKey: 'tool:world.mode.hint.auto' },
  { key: 'review', labelKey: 'tool:world.mode.review', hintKey: 'tool:world.mode.hint.review' },
]

/** 运行模式下拉（放在输入框底部那一条里）：三档是同一件事的三种约束强度，
 *  天天切的人少但要随手够得着——单独占一行太贵，改成"显示当前档 + 点开选"，
 *  档位说明同时进菜单正文和按钮 title。 */
function ModePicker({ mode, busy, onChange }: { mode: string; busy: boolean; onChange: (m: string) => void }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const cur = MODE_ITEMS.find((m) => m.key === mode) ?? MODE_ITEMS[MODE_ITEMS.length - 1]
  return (
    <div className="relative shrink-0">
      <button
        onClick={() => !busy && setOpen((v) => !v)}
        disabled={busy}
        title={`${t('tool:world.mode.label')}：${t(cur.labelKey)} —— ${t(cur.hintKey)}`}
        className="inline-flex items-center gap-1 h-6 px-1.5 rounded-control text-2xs text-textMuted hover:text-textPrimary hover:bg-elevated transition-colors disabled:opacity-60"
      >
        <ShieldAlert size={11} className="shrink-0" />
        <span className="max-w-[64px] truncate font-medium">{t(cur.labelKey)}</span>
        <ChevronDown size={11} className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-modal" onClick={() => setOpen(false)} />
          <MenuPanel className="absolute bottom-full left-0 mb-1 w-64 py-1 z-toast">
            {MODE_ITEMS.map((m) => (
              <MenuItem
                key={m.key}
                active={m.key === mode}
                onClick={() => { setOpen(false); if (m.key !== mode) onChange(m.key) }}
                title={t(m.hintKey)}
                className="py-2"
              >
                <span className="text-2xs font-medium">{t(m.labelKey)}</span>
                <span className="block text-3xs text-textMuted mt-0.5">{t(m.hintKey)}</span>
              </MenuItem>
            ))}
          </MenuPanel>
        </>
      )}
    </div>
  )
}

/** 思考气泡（DSH 式单行 Think 条，2026-08-16 借鉴 deepseek-harness ReasoningRow）：
 *  - 单行折叠条：图标 + "思考" + 摘要
 *  - 运行中：显示最新一行，自动跟随末尾（横向滚动），底部扫光动画
 *  - 完成：显示第一行作为摘要
 *  - 点击展开看全部
 */
function ReasoningBubble({ text, running }: { text: string; running?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const t = useT()
  const summaryRef = useRef<HTMLSpanElement>(null)

  // 运行中 = 最新一行；完成 = 第一行（与 DSH firstLine/latestLine 同语义）
  const summary = running
    ? (() => { const v = text.trimEnd(); const i = v.lastIndexOf('\n'); return i === -1 ? v : v.slice(i + 1) })()
    : (() => { const i = text.indexOf('\n'); return i === -1 ? text : text.slice(0, i) })()

  // 运行中自动滚动到末尾（跟随最新输出）
  useEffect(() => {
    if (running && summaryRef.current) {
      summaryRef.current.scrollLeft = summaryRef.current.scrollWidth - summaryRef.current.clientWidth
    }
  }, [running, summary])

  return (
    <div
      className={`world-msg max-w-[90%] mx-auto select-none text-2xs rounded-control overflow-hidden border ${
        expanded
          ? 'bg-elevated/60 border-border/60'
          : 'bg-elevated/40 border-border/40 cursor-pointer hover:bg-elevated/70 transition-colors'
      }`}
      onClick={() => !running && setExpanded((v) => !v)}
    >
      <div className="flex items-center gap-1.5 px-2 py-1">
        <Brain size={12} className="shrink-0 text-textMuted" />
        <span className="shrink-0 text-3xs text-textMuted font-medium">{t('tool:world.reasoning')}</span>
        <span className="shrink-0 w-px h-2.5 bg-border/60 mx-0.5" aria-hidden />
        <span
          ref={summaryRef}
          className={`flex-1 min-w-0 truncate text-textMuted ${running ? 'whitespace-nowrap overflow-hidden' : ''}`}
        >
          {summary || (running ? '…' : '')}
        </span>
        <ChevronDown size={11} className={`shrink-0 text-textMuted transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </div>
      {expanded && (
        <div className="px-2 pb-1.5 whitespace-pre-wrap text-textMuted max-h-72 overflow-y-auto border-t border-border/30 pt-1.5">
          {text}
        </div>
      )}
    </div>
  )
}

/** 左栏「会话」页签要的快照（数据源仍是本组件里那个 useWorldChat 实例） */
export interface WorldSessionsSnapshot {
  list: { id: string; title?: string; last_active_at?: string; pinned?: boolean }[]
  current: string
}

export interface WorldChatHandle {
  forceScrollToBottom: () => void
  unreadCount: number
  isInterrupted: boolean
  lastAiMsgId: number | null
  /** 会话动作：左栏列表借这几个方法驱动聊天状态。
   *  这样 useWorldChat 全页只有一个实例——否则左栏要自己再调一次接口、状态也会分叉。 */
  newSession: () => Promise<string | null>
  switchSession: (id: string) => Promise<boolean>
  /** 返回后端清洗后的名字（留空即清除命名），调用方一般不用它 */
  renameSession: (id: string, title: string) => Promise<string>
  /** 只能收藏当前会话（后端的 pin 落库对象就是当前会话） */
  togglePinCurrent: () => Promise<boolean>
  exportSession: (id: string, format: 'md' | 'json', name?: string) => Promise<void>
}

interface WorldChatPanelProps {
  wid: number
  onRefresh: () => void
  onMsg: (msg: string) => void
  /** 未读计数变化回调（父组件需要响应式更新标题栏徽章等） */
  onUnreadCountChange?: (count: number) => void
  /** 群视界机器人昵称（气泡标签用；缺省回退「世界 AI」） */
  creatorName?: string
  /** 世界当前运行模式（worlds.config.ai_mode；对话栏内可直接切） */
  aiMode?: string
  onModeChange?: (mode: string) => void
  /** 左栏「会话」页签：会话列表 / 当前会话变化时上报（父组件只展示，不复制状态） */
  onSessionsChange?: (snapshot: WorldSessionsSnapshot) => void
}

/**
 * 世界聊天面板（独立自包含组件）
 * - 内部调用 useWorldChat 管理所有聊天状态
 * - 所有聊天相关 UI 逻辑集中在此
 * - 通过 forwardRef 暴露 forceScrollToBottom 等接口给父组件
 * - 通过 onUnreadCountChange 回调通知父组件未读变化
 * - 打字/消息更新仅重渲染此组件，不触发父组件
 */
const WorldChatPanel = memo(forwardRef<WorldChatHandle, WorldChatPanelProps>(({ wid, onRefresh, onMsg, onUnreadCountChange, creatorName, aiMode, onModeChange, onSessionsChange }, ref) => {
  const t = useT()
  // 内容列宽：列宽靠 ResizeObserver 量（工具条/侧栏变化都要重新收敛），
  // 内容列宽经 CSS 变量下发——拖拽时直接改变量，不触发本组件（含整段消息列表）重渲
  const [measureRef, columnWidth] = useElementWidth()
  // 变量宿主（display:contents 那层）只管挂 CSS 变量；列宽由消息区的量测 ref 报（= 本面板整列宽）
  const colHostRef = useRef<HTMLDivElement | null>(null)
  const setColumnHost = useCallback((el: HTMLDivElement | null) => { colHostRef.current = el }, [])
  const { contentWidth, dragging: widthDragging, onHandleDown } = useContentColumnWidth(columnWidth, colHostRef)
  // 运行模式（对话栏内切换；与设计页配置弹窗是同一个后端字段，切换后回调父组件同步）
  const [mode, setMode] = useState(aiMode || 'review')
  const [modeBusy, setModeBusy] = useState(false)
  useEffect(() => { if (aiMode) setMode(aiMode) }, [aiMode])
  const switchMode = useCallback(async (next: string) => {
    setModeBusy(true)
    try {
      await api.put<{ ai_mode: string }>(`/worlds/${wid}/ai-mode`, { mode: next })
      setMode(next)
      onModeChange?.(next)
      const item = MODE_ITEMS.find((m) => m.key === next)
      onMsg?.(item ? t(item.hintKey) : next)
    } catch (e: any) {
      onMsg?.(`切换运行模式失败: ${e?.message || e}`)
    } finally {
      setModeBusy(false)
    }
  }, [wid, onModeChange, onMsg, t])
  // ── 内部管理所有聊天状态 ──
  const chat = useWorldChat({ wid, onRefresh, onMsg })

  // ── 计算派生状态 ──
  const isInterrupted = useMemo(() => {
    for (let i = chat.chatMsgs.length - 1; i >= 0; i--) {
      const m = chat.chatMsgs[i]
      if (m.role === 'ai') return String(m.content || '').includes('对话中断')
      if (m.role === 'user') continue
    }
    return false
  }, [chat.chatMsgs])

  const lastAiMsgId = useMemo(() => {
    for (let i = chat.chatMsgs.length - 1; i >= 0; i--) {
      if (chat.chatMsgs[i].role === 'ai') return chat.chatMsgs[i].id
    }
    return null
  }, [chat.chatMsgs])

  // ── 会话快照上报给父组件（左栏「会话」页签用） ──
  // 依赖就是 hook 里的会话状态本身：值没变时引用不变，父组件的 setState 会被 Object.is 直接吃掉，
  // 不会因为"上报"多渲染一轮
  useEffect(() => {
    onSessionsChange?.({ list: chat.sessionList, current: chat.currentSession })
  }, [chat.sessionList, chat.currentSession, onSessionsChange])

  // ── 通知父组件未读计数变化 ──
  useEffect(() => {
    onUnreadCountChange?.(chat.unreadCount)
  }, [chat.unreadCount, onUnreadCountChange])


  /** 下载会话记录：Markdown / JSON（文件名以服务端 Content-Disposition 为准） */
  const downloadSession = useCallback(async (id: string, format: 'md' | 'json', name?: string) => {
    try {
      await chat.exportSession(id, format, `${name || id}.${format}`)
    } catch (e: any) {
      onMsg?.(t('tool:world.session.exportFailed') + (e?.message || e))
    }
  }, [chat, onMsg, t])

  // ── 暴露给父组件的接口（含会话动作：左栏列表借它驱动本组件的聊天状态）。
  //    放在 downloadSession 之后：这里要把它包进接口，函数得先有定义 ──
  useImperativeHandle(ref, () => ({
    forceScrollToBottom: chat.forceScrollToBottom,
    unreadCount: chat.unreadCount,
    isInterrupted,
    lastAiMsgId,
    newSession: chat.newSession,
    switchSession: chat.switchSession,
    renameSession: chat.renameSession,
    togglePinCurrent: chat.togglePin,
    exportSession: downloadSession,
  }), [chat.forceScrollToBottom, chat.unreadCount, isInterrupted, lastAiMsgId, chat.newSession, chat.switchSession, chat.renameSession, chat.togglePin, downloadSession])

  // ── 本地输入状态（打字时只有此组件重渲染） ──
  const [localInput, setLocalInput] = useState('')
  const localInputRef = useRef('')

  // ── 附件（图片可发给 AI；上传走与主站聊天共用的 hook） ──
  // imagesOnly：非图片进不了多模态，让用户当场看见"仅支持图片"而不是发出去白费一轮
  const attachments = useAttachmentUpload({ imagesOnly: true })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const handlePickFiles = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) attachments.pick(e.target.files)
    e.target.value = ''  // 清空，便于重复选同一文件
  }, [attachments.pick])

  // ── 本地命令检测状态 ──
  const [localCmdActive, setLocalCmdActive] = useState(false)
  const [localCmdQuery, setLocalCmdQuery] = useState('')
  const [localCmdIdx, setLocalCmdIdx] = useState(0)
  const localCmdFiltered = useMemo(() =>
    localCmdQuery ? chat.worldCommands.filter((c) => c.cmd.startsWith('/' + localCmdQuery)) : chat.worldCommands
  , [localCmdQuery, chat.worldCommands])

  const clearLocalInput = useCallback(() => {
    setLocalInput('')
    localInputRef.current = ''
    setLocalCmdActive(false)
    // 顺手把自适应高度归零：内容清了框还撑着，看起来像没发出去
    const ta = chat.chatInputRef.current
    if (ta) ta.style.height = ''
  }, [chat.chatInputRef])

  const handleSubmit = useCallback((text: string) => {
    const t = text.trim()
    const atts = attachments.ready
    if (!t && atts.length === 0) return
    chat.submitText(t, atts.length ? atts : undefined)
    attachments.clear()
    clearLocalInput()
  }, [chat, attachments, clearLocalInput])

  const handleCmdSelect = useCallback((cmd: string) => {
    chat.submitText(cmd)
    clearLocalInput()
  }, [chat, clearLocalInput])

  // 重新生成（DSH 式 branch 语义）：截断该 AI 回复及其后的历史，重发最后一条用户消息
  const handleRegenerate = useCallback(async (aiMsgId: number) => {
    if (chat.chatSending || chat.chatProcessing) return
    // 找该 AI 回复之前的最后一条用户消息（作为重发对象）
    const idx = chat.chatMsgs.findIndex((m) => m.id === aiMsgId)
    const before = idx >= 0 ? chat.chatMsgs.slice(0, idx) : chat.chatMsgs
    const lastUser = [...before].reverse().find((m) => m.role === 'user' && !m.pending)
    if (!lastUser || !lastUser.content) return
    try {
      // 1) 后端截断：删除该消息及其之后的所有消息
      const r = await api.post<{ messages: ChatMsg[] }>(`/worlds/${chat.wid}/chat/regenerate`, { session_id: String(aiMsgId) })
      if (Array.isArray(r.messages)) {
        chat.setChatMsgs(r.messages)
        chat.forceScrollToBottom?.()
      }
      // 2) 重发最后一条用户消息 → 生成新回复（取代旧回复）
      chat.submitText(lastUser.content, lastUser.attachments)
    } catch (err: any) {
      if (onMsg) onMsg(`重新生成失败：${err?.message || '未知错误'}`)
    }
  }, [chat, onMsg])

  /** 建议卡不再"点一下就发"：先弹窗确认（2026-09-17 用户：飞机太容易误触），
   *  弹窗里同时提醒"想改就点 ＋ 插入输入框"。行点击与飞机共用这一处判断。 */
  const confirmAndSendSuggestion = useCallback(async (q: string) => {
    const ok = await confirmAsync({
      key: `world.suggest:${q}`,
      title: t('tool:world.suggest.title'),
      confirmText: t('tool:world.suggest.confirm'),
      message: (
        <>
          <div className="rounded-control bg-elevated border border-border px-3 py-2 text-xs text-textPrimary whitespace-pre-wrap break-words">{q}</div>
          <div className="mt-2 text-3xs text-textMuted">{t('tool:world.suggest.modifyHint')}</div>
        </>
      ),
    })
    if (ok) handleSubmit(q)
  }, [handleSubmit, t])

  const handleInsertSuggestion = useCallback((q: string) => {
    const next = localInputRef.current ? localInputRef.current + ' ' + q : q
    localInputRef.current = next
    setLocalInput(next)
    requestAnimationFrame(() => {
      const ta = chat.chatInputRef.current
      if (ta) {
        ta.focus()
        const pos = next.length
        ta.setSelectionRange(pos, pos)
      }
    })
  }, [chat])

  // 同一段思考只显示一次：2026-09-15 之前的落库缺陷会把一段思考既落成 note、
  // 又挂在最终回复上（历史数据里已经存在这种重复）。向前回溯到本轮起点（遇 user 停），
  // 发现同一段思考就不再重复渲染——只吞掉重复的那一份，正常轮次不受影响。
  const reasoningAlreadyShown = (msgIndex: number, reasoning?: string) => {
    if (!reasoning) return false
    for (let i = msgIndex - 1; i >= 0; i--) {
      const prev = chat.chatMsgs[i]
      if (prev.role === 'user') break
      if (prev.reasoning === reasoning && (prev.role === 'note' || prev.role === 'ai')) return true
    }
    return false
  }

  // ── 渲染消息列表 ──
  const renderMessage = (m: typeof chat.chatMsgs[number], msgIndex: number) => {
    const isLastAi = m.role === 'ai' && m.id === lastAiMsgId
    // 正文上方紧跟思考气泡（上一条 note 且无正文）→ 省略「世界 AI」标签（思考已标识 AI 身份）
    const prevIsReasoning = msgIndex > 0
      && chat.chatMsgs[msgIndex - 1].role === 'note'
      && !chat.chatMsgs[msgIndex - 1].content
      && !!chat.chatMsgs[msgIndex - 1].reasoning
    // 老数据兼容：重复挂载的同一段思考只显示第一份（见 reasoningAlreadyShown）
    const shownReasoning = reasoningAlreadyShown(msgIndex, m.reasoning) ? '' : m.reasoning

    if (m.role === 'tool') {
      // 工具状态气泡：running（正在执行 XX）→ update（进度）→ done（完成）
      // 结构化字段走 i18n（tool:toolName.{name} + tool:tool.{status} 模板插值）；旧格式直接显示 content
      let label = m.content
      // 工具中文名：i18n 词条优先（可本地化），没有词条就用插件自带的 label，再回退原始工具名
      const nameKey = m.tool_name ? `tool:toolName.${m.tool_name}` : ''
      const nameLabel = nameKey ? t(nameKey) : ''
      const toolName = nameLabel && nameLabel !== nameKey ? nameLabel : (m.tool_label || m.tool_name || '')
      if (m.tool_name) {
        if (m.tool_status === 'running') {
          label = t('tool:tool.running', { name: `${toolName}${m.tool_args ? `：${m.tool_args}` : ''}` })
        } else if (m.tool_status === 'update') {
          label = m.content || t('tool:tool.update', { summary: `${toolName}…` })
        } else {
          label = m.content || t('tool:tool.done', { summary: `${toolName} ${m.error ? '执行失败' : '执行完成'}` })
        }
      }
      return (
        <ToolBubble
          key={m.id}
          // 完成态才单独挂工具名（运行/进度态的文案里已含名字，避免重复）
          name={m.tool_status === 'running' || m.tool_status === 'update' ? '' : toolName}
          label={label}
          detail={m.tool_detail}
          error={m.error ?? m.is_error}
          running={m.tool_status === 'running' || m.tool_status === 'update'}
          icon={toolIcon(m.content)}
        />
      )
    }

    // 思考单行条（DSH 式，2026-08-16）：只有思考没正文（note 且 content 空）——
    // 运行中显示最新一行+扫光，完成显示首行摘要，点击展开
    if (m.role === 'note' && !m.content && m.reasoning) {
      if (!shownReasoning) return null   // 重复的同一段思考：上面已经有一条
      const reasoningRunning = (chat.chatSending || chat.chatProcessing) && m.id === lastAiMsgId
      return <ReasoningBubble key={m.id} text={shownReasoning} running={reasoningRunning} />
    }

    return (
      // group：让下方操作条（复制/重新生成）在悬停整条消息时显形。
      // ⚠️ 必须用**匿名** group——子元素里的 group/details 是命名组，只响应 group-hover/details:
      <div key={m.id} className="space-y-2 group">
        <div className={`world-msg text-sm max-w-[90%] p-2 rounded-control ${m.error ? 'bg-rose-500/10 border border-rose-500/30 text-rose-400' : m.role === 'user' ? 'bg-primary-500/20 ml-auto' : 'bg-elevated/80'}`}>
          <div className="text-3xs text-textMuted mb-0.5">{m.error ? '错误' : m.role === 'user' ? (m.pending ? '我（排队中，发送后生效）' : '我') : (prevIsReasoning ? '' : (creatorName || '世界 AI'))}</div>
          {!m.error && (m.role === 'ai' || m.role === 'note') && !!shownReasoning && (
            <details className="group/details mb-1.5">
              <summary className="flex items-center gap-1 text-3xs text-textMuted cursor-pointer select-none hover:text-textSecondary list-none [&::-webkit-details-marker]:hidden">
                <ChevronRight size={11} className="transition-transform group-open/details:rotate-90" />
                <Brain size={11} className="text-textMuted" />
                思考过程
              </summary>
              <div className="text-xs text-textMuted mt-1 whitespace-pre-wrap bg-elevated/70 rounded p-2">{shownReasoning}</div>
            </details>
          )}
          {m.role === 'user' && !!m.attachments?.length && (
            <div className="flex flex-wrap gap-1.5 mb-1">
              {m.attachments.map((att, ai) => {
                const url = `/api/fs/download/${att.file_id}?token=${localStorage.getItem('access_token') || ''}`
                return isImageAttachment(att) ? (
                  <a key={ai} href={url} target="_blank" rel="noreferrer" title={att.name}>
                    <img src={url} alt={att.name} className="max-h-40 max-w-full rounded border border-border/60" />
                  </a>
                ) : (
                  <span key={ai} className="text-3xs text-textMuted">{att.name}</span>
                )
              })}
            </div>
          )}
          {m.content ? <MarkdownContent content={m.content} /> : m.role === 'ai' ? (
            shownReasoning ? (
              <span className="opacity-50 text-xs italic">
                {(() => { const r = shownReasoning.trim(); return r.length > 60 ? r.slice(-60) + '…' : r || '思考中…' })()}
              </span>
            ) : m.reasoning ? null : (
              <span className="inline-flex gap-0.5">
                <span className="w-1 h-1 bg-primary-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1 h-1 bg-primary-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1 h-1 bg-primary-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </span>
            )
          ) : null}
        </div>

        {/* 消息操作条（DSH 式 MessageIconActions，2026-08-16）：复制 + 重新生成 */}
        {(m.role === 'ai' || m.role === 'note') && m.content && !m.pending && (
          // 键盘可达 + 无悬停设备（触屏）常显：光靠 group-hover 在触屏上永远点不到
          <div className="flex items-center gap-0.5 pl-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity">
            <button
              onClick={() => { void copyText(m.content || '') }}
              className="p-1 rounded text-textMuted hover:text-textSecondary hover:bg-elevated transition-colors"
              title="复制回复"
            ><Copy size={12} /></button>
            <button
              onClick={() => handleRegenerate(m.id)}
              className="p-1 rounded text-textMuted hover:text-textSecondary hover:bg-elevated transition-colors"
              title="重新生成（截断此回复并重发，旧回复被取代）"
            ><RefreshCw size={12} /></button>
          </div>
        )}

        {/* 中断 → "你可以：继续" */}
        {isLastAi && isInterrupted && !chat.chatSending && !chat.chatProcessing && (
          <div className="space-y-1.5 pl-1 w-full max-w-[420px]">
            <div className="text-3xs text-textMuted">你可以：</div>
            <div className={`flex items-stretch ${INSET_CARD} overflow-hidden w-full`}>
              <button
                onClick={() => handleSubmit('继续')}
                className="flex-1 min-w-0 px-2.5 py-1.5 text-left text-xs text-textSecondary hover:bg-primary-500/20 hover:text-primary-500 dark:hover:text-primary-300 transition-colors truncate"
                title="继续之前的工作"
              >继续之前的工作</button>
              <div className="w-px bg-border shrink-0" />
              <button
                onClick={(e) => { e.stopPropagation(); handleSubmit('继续') }}
                className="px-2 flex items-center text-textMuted hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/20 transition-colors shrink-0"
                title="发送"
              ><Send size={11} /></button>
            </div>
          </div>
        )}

        {/* 建议列表 */}
        {(isLastAi || chat.chatMsgs.length === 0) && !isInterrupted && chat.suggestions.length > 0 && !chat.chatSending && !chat.chatProcessing && (
          <div className="space-y-1.5 pl-1 w-full max-w-[420px]">
            <div className="text-3xs text-textMuted">你可以：</div>
            {chat.suggestions.map((q, i) => (
              <div key={i} className={`flex items-stretch ${INSET_CARD} overflow-hidden w-full`}>
                <button
                  onClick={() => confirmAndSendSuggestion(q)}
                  className="flex-1 min-w-0 px-2.5 py-1.5 text-left text-xs text-textSecondary hover:bg-primary-500/20 hover:text-primary-500 dark:hover:text-primary-300 transition-colors truncate"
                  title={q}
                >{q}</button>
                <div className="w-px bg-border shrink-0" />
                <button
                  onClick={(e) => { e.stopPropagation(); confirmAndSendSuggestion(q) }}
                  className="px-2 flex items-center text-textMuted hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/20 transition-colors shrink-0"
                  title="发送这条"
                ><Send size={11} /></button>
                <div className="w-px bg-border shrink-0" />
                <button
                  onClick={(e) => { e.stopPropagation(); handleInsertSuggestion(q) }}
                  className="px-2 flex items-center text-textMuted hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/20 transition-colors shrink-0"
                  title="插入到输入框（追加，不覆盖）"
                ><Plus size={12} /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // ── 空状态建议 ──
  const renderEmptySuggestions = () => (
    <div className="text-center mt-8 space-y-3">
      <div className="text-xs text-textMuted">暂无消息，从下面开始探索：</div>
      {chat.suggestions.length > 0 && !chat.chatSending && !chat.chatProcessing && (
        <div className="flex flex-col items-center gap-1.5 w-full max-w-[420px]">
          {chat.suggestions.map((q, i) => (
            <div key={i} className={`flex items-stretch ${INSET_CARD} overflow-hidden w-full`}>
              <button
                onClick={() => confirmAndSendSuggestion(q)}
                className="flex-1 min-w-0 px-2.5 py-1.5 text-left text-xs text-textSecondary hover:bg-primary-500/20 hover:text-primary-500 dark:hover:text-primary-300 transition-colors truncate"
                title={q}
              >{q}</button>
              <div className="w-px bg-border shrink-0" />
              <button
                onClick={(e) => { e.stopPropagation(); confirmAndSendSuggestion(q) }}
                className="px-2 flex items-center text-textMuted hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/20 transition-colors shrink-0"
                title="发送这条"
              ><Send size={11} /></button>
              <div className="w-px bg-border shrink-0" />
              <button
                onClick={(e) => { e.stopPropagation(); handleInsertSuggestion(q) }}
                className="px-2 flex items-center text-textMuted hover:text-primary-500 dark:hover:text-primary-300 hover:bg-primary-500/20 transition-colors shrink-0"
                title="插入到输入框（追加，不覆盖）"
              ><Plus size={12} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  // ── 命令下拉菜单 ──
  const renderCmdMenu = () => (
    <MenuPanel className="absolute bottom-full left-3 mb-1 w-64 max-h-40 overflow-y-auto py-1 z-modal">
      {localCmdFiltered.map((c, i) => (
        <MenuItem
          key={c.cmd}
          active={i === localCmdIdx}
          className="py-2"
          onMouseDown={(e) => { e.preventDefault(); handleCmdSelect(c.cmd) }}
        >
          <span className="font-mono text-xs text-textPrimary">{c.cmd}</span>
          <span className="block text-3xs text-textMuted">{c.desc}</span>
        </MenuItem>
      ))}
    </MenuPanel>
  )

  return (
    <>
      {/* 内容列宽变量挂这一层：display:contents 不产生盒子，只负责把变量继承给下面所有内容。
          拖拽期间只改这个变量——本组件（含整段消息列表）一帧都不重渲 */}
      <div ref={setColumnHost} className="contents" style={{ [CONTENT_W_VAR]: `${contentWidth}px` } as React.CSSProperties}>
      {/* 消息列表：外层不滚动，专门用来挂拖拽提示层（放进滚动容器会随内容滚走）；
          内层才是滚动容器——chatListRef 必须挂在滚动元素上（它读 scrollHeight/scrollTop）。
          滚动容器铺满整列（滚动条贴列右边缘），居中靠"左右内边距 = 两侧留白"实现（DSH scrollBody/column 同一个意思）：
          这样滚轮落在两侧留白里也能滚。列宽用 ResizeObserver 量，量多少算多少 */}
      <div ref={measureRef} className="flex-1 min-h-0 relative" {...attachments.zoneProps('list')}>
        <DropMask {...attachments.dropState('list')} label="拖动到此处上传图片" />
        <div
          ref={chat.chatListRef}
          className="absolute inset-0 overflow-y-auto py-3 space-y-2 [scrollbar-gutter:stable]"
          style={{
            paddingLeft: `max(0px, calc((100% - var(${CONTENT_W_VAR})) / 2))`,
            paddingRight: `max(0px, calc((100% - var(${CONTENT_W_VAR})) / 2))`,
          }}
        >
          {chat.chatLoadingOlder && <div className="text-3xs text-textMuted text-center py-1">加载更早消息…</div>}
          {chat.chatMsgs.length === 0 ? renderEmptySuggestions() : chat.chatMsgs.map((m, i) => renderMessage(m, i))}

          {/* 回到底部 / 新消息按钮：不在底部（或未读>0，或列表不可滚动时给入口）才显示；可滚动且在底部隐藏
              sticky 固定在聊天列表视口右下角（输入区正上方）：列表滚动时不动，不随消息内容滚 */}
          {(!chat.isAtBottom || !chat.chatCanScroll || chat.unreadCount > 0) && (
            <div className="sticky bottom-3 flex justify-end pointer-events-none z-drawer">
              <button
                onClick={() => chat.scrollToBottom(true)}
                className={`pointer-events-auto flex items-center justify-center gap-1 px-3 h-8 rounded-full shadow-lg transition-all ${
                  chat.unreadCount > 0
                    ? 'bg-rose-500 hover:bg-rose-600 text-white border border-rose-400 animate-bounce'
                    : 'bg-elevated border border-border text-textSecondary hover:text-textPrimary hover:bg-surface'
                }`}
                title="回到底部"
              >
                {chat.unreadCount > 0 ? (
                  <>
                    <ArrowDown size={14} />
                    <span className="text-xs font-semibold">{chat.unreadCount} 条新消息</span>
                  </>
                ) : (
                  <ArrowDown size={14} />
                )}
              </button>
            </div>
          )}
        </div>
        {/* 内容列宽拖条（学 DSH 的 WidthHandle）：落在两侧留白里、比内容列边缘再外 24px；
            宽度 min(40px, 留白 - 48px)，留白不够时自然收成 0 —— 拖到头也还留得下拖回来的把手 */}
        {(['left', 'right'] as const).map((side) => (
          <div
            key={side}
            role="separator"
            aria-orientation="vertical"
            onMouseDown={onHandleDown(side)}
            title={t('tool:world.chat.width')}
            className={`absolute top-0 bottom-0 z-overlay cursor-col-resize transition-colors ${widthDragging ? 'bg-primary-500/40' : 'hover:bg-primary-500/30'}`}
            style={{
              width: `max(0px, min(40px, calc((100% - var(${CONTENT_W_VAR})) / 2 - 48px)))`,
              ...(side === 'left'
                ? { right: `calc(50% + var(${CONTENT_W_VAR}) / 2 + 24px)` }
                : { left: `calc(50% + var(${CONTENT_W_VAR}) / 2 + 24px)` }),
            }}
          />
        ))}
      </div>

      {/* 输入区（图片可直接拖进来放下，与点回形针等价） */}
      <div className="p-3 border-t border-border relative" {...attachments.zoneProps('input')} {...attachments.pasteProps}>
        <DropMask {...attachments.dropState('input')} label="拖动到此处上传图片" />
        {/* 排队消息（AI 处理中，输入框上方弹窗展示）：面板与群视界对话共用同一份零件
            （components/shared/ChatDialogs），宽度仍按内容列宽口径对齐 */}
        <PendingQueuePanel
          items={chat.pendingItems}
          onRemove={(i) => chat.setPendingItems((items) => items.filter((_, j) => j !== i))}
          style={{ maxWidth: `calc(var(${CONTENT_W_VAR}) + 32px)` }}
        />
        {localCmdActive && localCmdFiltered.length > 0 && renderCmdMenu()}
        <AttachmentChips items={attachments.items} onRemove={attachments.remove} />
        {/* 单层容器（学 DSH）：textarea 不再自带边框，聚焦高亮只在外层亮一次。
            原来是外框套内框，聚焦只亮内层那条，看着像没聚焦 */}
        {/* 输入框跟内容列同宽（DSH：内容列宽 + 32px 侧余），窄面板下自然回落成整宽 */}
        <div
          className="mx-auto w-full rounded-2xl border border-border bg-elevated/40 transition-colors focus-within:border-primary-500/60 focus-within:bg-surface"
          style={{ maxWidth: `calc(var(${CONTENT_W_VAR}) + 32px)` }}
        >
        <textarea
          ref={chat.chatInputRef}
          value={localInput}
          onChange={(e) => {
            const val = e.target.value
            localInputRef.current = val
            setLocalInput(val)
            // 多行自适应：先归零再按 scrollHeight 长高，到 240px 为止（再长就内部滚动）
            const el = e.target
            el.style.height = 'auto'
            el.style.height = Math.min(el.scrollHeight, 240) + 'px'
            const before = val.slice(0, e.target.selectionStart)
            const m = before.match(/^\/\w*$/)
            if (m) { setLocalCmdQuery(before.slice(1)); setLocalCmdActive(true); setLocalCmdIdx(0) }
            else if (localCmdActive) setLocalCmdActive(false)
          }}
          onKeyDown={(e) => {
            if (localCmdActive && localCmdFiltered.length > 0) {
              if (e.key === 'ArrowDown') { e.preventDefault(); setLocalCmdIdx((i) => (i + 1) % localCmdFiltered.length); return }
              if (e.key === 'ArrowUp') { e.preventDefault(); setLocalCmdIdx((i) => (i - 1 + localCmdFiltered.length) % localCmdFiltered.length); return }
              if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); handleCmdSelect(localCmdFiltered[localCmdIdx].cmd); return }
              if (e.key === 'Escape') { e.preventDefault(); setLocalCmdActive(false); return }
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              const text = localInputRef.current
              if (!text.trim() && attachments.ready.length === 0) return
              handleSubmit(text)
            }
          }}
          rows={2}
          placeholder={(chat.chatSending || chat.chatProcessing) ? t('tool:world.input.placeholder.busy') : t('tool:world.input.placeholder')}
          className="w-full bg-transparent text-sm px-3 pt-2.5 pb-1 outline-none resize-none placeholder:text-textMuted"
        />
        {/* 底部一条：左＝＋附件 / 运行模式 / 通俗模式 / 说明，右＝发送。都挤在同一行，不另占一行高度 */}
        <div className="flex items-center gap-1 px-2 pb-1.5">
          <input ref={fileInputRef} type="file" multiple accept="image/*" className="hidden" onChange={handlePickFiles} />
          <IconButton size="sm" icon={<Plus size={14} />} label={t('tool:world.input.attach')} onClick={() => fileInputRef.current?.click()} />
          <ModePicker mode={mode} busy={modeBusy} onChange={switchMode} />
          <ExpressionModeSwitch />
          {/* 常驻说明收进 ? 的 title：绝大多数轮次用不到，不该天天占一行 */}
          <IconButton size="sm" icon={<HelpCircle size={12} />} label={t('tool:world.hint.billing')} />
          {(chat.chatSending || chat.chatProcessing) && (
            <span className="hidden sm:inline min-w-0 truncate text-3xs text-textMuted">{t('tool:world.input.busyHint')}</span>
          )}
          <div className="flex-1" />
          <button
            onClick={() => handleSubmit(localInputRef.current)}
            disabled={!localInput.trim() && attachments.ready.length === 0}
            className="shrink-0 inline-flex items-center gap-1 h-7 px-2.5 rounded-control bg-primary-500 hover:bg-primary-600 text-white text-xs font-medium transition-colors disabled:opacity-40"
          >
            {(chat.chatSending || chat.chatProcessing) ? t('tool:world.input.queue') : t('tool:world.input.send')}
            <Send size={12} />
          </button>
        </div>
        </div>
      </div>

      {/* 审批弹窗：一次只显示队首（后端同一时刻几乎只会挂一个待审批） */}
      {chat.approvals.length > 0 && (
        <ApprovalDialog
          // key：换了一条审批就重挂载，输入框不会带着上一条残留的字
          key={chat.approvals[0].approval_id}
          approval={chat.approvals[0]}
          onDecide={(ok, note) => chat.resolveApproval(chat.approvals[0].approval_id, ok, note)}
          onTouch={chat.touchApproval}
        />
      )}
      </div>
    </>
  )
}))

WorldChatPanel.displayName = 'WorldChatPanel'

export default WorldChatPanel
