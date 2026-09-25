/**
 * 世界 AI 对话 hook — 聊天状态 + 流式发送 + 排队 + 建议按钮 + 斜杠命令
 * 从 WorldDesignPage 拆分（2026-08-06 重构）
 */
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject, type Dispatch, type SetStateAction, type UIEvent } from 'react'
import { api } from '../api/client'
import type { ReadyAttachment } from './useAttachmentUpload'
import { BOTTOM_THRESHOLD, useStickToBottom } from './useStickToBottom'

// AI 处理中状态的初始值：状态检查（/chat/status）返回前一律按"处理中"对待，
// 消息走插入队列，避免与仍在运行的 turn 冲突。
const CHAT_PROCESSING_INITIAL = true

// 空闲时的状态复查间隔：轮次未必由本页面发起（群里唤起常驻世界、别的标签页发消息），
// 审阅模式的审批弹窗就是这么冒出来的——只在自己发消息后看状态会漏掉，所以空闲也要慢轮询。
const IDLE_RECHECK_MS = 10000



// 世界 AI 对话消息（世界级会话，非 DM；reasoning = 思考过程；tool = 工具执行结果；note = 中间叙述）
export interface ChatMsg {
  id: number
  role: 'user' | 'ai' | 'tool' | 'note'
  content: string
  reasoning?: string
  error?: boolean
  /** 排队中（AI 处理时发送，尚未真正发出；流结束后自动发送并刷新为正式消息） */
  pending?: boolean
  created_at?: string
  /** 工具状态事件（2026-08-13）：tool_id 定位气泡，多状态原地更新 */
  tool_id?: string
  tool_name?: string
  /** 工具中文名（后端插件自带；前端 i18n 有词条时优先用词条） */
  tool_label?: string
  tool_status?: 'running' | 'update' | 'done'
  tool_args?: string
  /** 点开卡片后的详细说明（参数 + 结果；UI 专用，不进 LLM 上下文） */
  tool_detail?: string
  /** 工具执行失败（落库后刷新保持红色；2026-08-13） */
  is_error?: boolean
  /** 消息附件（图片在气泡里渲染成图；2026-09-13 新增） */
  attachments?: ReadyAttachment[]
}

/** 排队弹窗条目（AI 处理中暂存，之后按序发送） */
export interface PendingItem {
  kind: 'msg' | 'cmd'
  text: string
  attachments?: ReadyAttachment[]
}

/** 发往 /worlds/{id}/chat 的一条消息（items 契约，对应后端 ChatItem） */
export interface OutgoingItem {
  text: string
  attachments?: ReadyAttachment[]
}

// SSE 事件前缀（与后端 world_chat_service 的 yield 格式一一对应；解析用常量避免魔法数字）
const EV = {
  INSERTED: '[INSERTED]',  // 信号：排队消息已插入（不计历史）→ 清排队弹窗
  INSERT: '[INSERT]',      // 消息：已落库（记历史）→ 画用户气泡
  APPROVAL: '[APPROVAL]',  // 审批弹窗：审阅/计划模式下 AI 的敏感操作等用户点按钮
} as const

/** 审批弹窗（后端 world_ai_mode.request_approval 下发；status=resolved 即关闭） */
export interface Approval {
  approval_id: string
  /** 事件类型关键词：download | delete | modify | plan | other */
  kind: string
  title: string
  /** 一行摘要（动哪个文件/哪个地址） */
  detail?: string
  /** 用户真正要看的内容：文件内容、计划正文、AI 的补充说明 */
  body?: string
  /** body 怎么渲染：markdown（散文/计划）| code（代码块）| text（纯文本） */
  body_format?: 'markdown' | 'code' | 'text'
  body_lang?: string
  /** 距超时还剩多少秒（服务端唯一口径；输入框里打字的心跳会把它续回窗口大小） */
  expires_in?: number
}

/** 解析 `[PREFIX]{json}` 事件体；前缀不匹配/JSON 坏返回 null */
function parseEvent<T>(payload: string, prefix: string): T | null {
  if (!payload.startsWith(prefix)) return null
  try {
    return JSON.parse(payload.slice(prefix.length)) as T
  } catch {
    return null
  }
}

// 斜杠命令列表兜底（后端 COMMAND_SPECS 未下发时的首帧默认值）
// 权威来源是后端 world_chat_commands.COMMAND_SPECS，经 GET /chat 的 commands 字段下发——
// 新增命令只改后端一处，前端不再需要同步维护
// mid_turn：能否在 AI 工具轮进行中直接插入本轮（缺省 false = 等本轮结束）
export interface CmdSpec { cmd: string; desc: string; mid_turn?: boolean }

export const WORLD_COMMANDS: CmdSpec[] = [
  { cmd: '/new', desc: '开新对话（旧对话保存，可切回）' },
  { cmd: '/sessions', desc: '列出所有会话（id + 时间 + 收藏）' },
  { cmd: '/use <id>', desc: '切回指定会话继续对话' },
  { cmd: '/pin', desc: '收藏当前会话（最多 16 个，不被清理）' },
  { cmd: '/unpin', desc: '取消收藏当前会话' },
  { cmd: '/clear', desc: '清空当前会话上下文（保留长期记忆）' },
  { cmd: '/compact', desc: '压缩当前会话上下文为摘要' },
]

interface UseWorldChatOptions {
  wid: number
  /** 世界信息刷新（AI 工具可能改过世界，回复结束后调用） */
  onRefresh: () => void
  /** 顶部提示消息 */
  onMsg: (msg: string) => void
}

export interface UseWorldChatReturn {
  chatMsgs: ChatMsg[]
  chatInput: string
  setChatInput: (v: string) => void
  chatSending: boolean
  chatProcessing: boolean
  chatHasMore: boolean
  chatLoadingOlder: boolean
  /** 列表元素回调 ref（多个实例共存时逐个收集）——来自共享的 useStickToBottom */
  chatListRef: (el: HTMLDivElement | null) => void
  chatInputRef: RefObject<HTMLTextAreaElement | null>
  pendingItems: PendingItem[]
  setPendingItems: Dispatch<SetStateAction<{ kind: 'msg' | 'cmd'; text: string }[]>>
  suggestions: string[]
  cmdActive: boolean
  setCmdActive: (v: boolean) => void
  cmdQuery: string
  setCmdQuery: (v: string) => void
  cmdIdx: number
  setCmdIdx: Dispatch<SetStateAction<number>>
  cmdFiltered: CmdSpec[]
  /** 命令目录（后端 COMMAND_SPECS 下发，供补全与路由判定共用） */
  worldCommands: CmdSpec[]
  submitText: (text: string) => void
  insertSuggestion: (q: string) => void
  isAtBottom: boolean
  chatCanScroll: boolean
  currentSession: string
  sessionList: { id: string; title?: string; last_active_at?: string; pinned?: boolean }[]
  switchSession: (sid: string) => Promise<boolean>
  newSession: () => Promise<string | null>
  togglePin: () => Promise<boolean>
  scrollToBottom: (force?: boolean) => void
  forceScrollToBottom: () => void
  unreadCount: number
  /** 待用户点按钮的审批项（队列，通常一次只有一条；刷新后由 /chat/status 恢复） */
  approvals: Approval[]
  resolveApproval: (approvalId: string, approved: boolean, note?: string) => Promise<void>
  /** 审批心跳（打字时调用）：返回剩余秒数，0 = 已被处理 */
  touchApproval: (approvalId: string) => Promise<number>
  /** 给会话改名（sessionId = 列表里任意一场）；返回规范化后的名字，'' = 已清除命名 */
  renameSession: (sessionId: string, title: string) => Promise<string>
  /** 下载会话记录（md / json）；文件名以服务端 Content-Disposition 为准 */
  exportSession: (sessionId: string, format: 'md' | 'json', fallbackName: string) => Promise<void>
}

export function useWorldChat({ wid, onRefresh, onMsg }: UseWorldChatOptions) {
  const [chatMsgs, setChatMsgs] = useState<ChatMsg[]>([])
  const [chatInput, setChatInput] = useState('')
  const [chatSending, setChatSending] = useState(false)
  const [chatProcessing, setChatProcessing] = useState(CHAT_PROCESSING_INITIAL)
  const [pendingItems, setPendingItems] = useState<PendingItem[]>([])  // AI 处理中排队消息（msg 一起发；cmd 串行执行）
  const [suggestions, setSuggestions] = useState<string[]>([])  // "你可以"建议（AI 生成 / 兜底 / 预设）
  // 待审批项（审阅/计划模式）：后端弹窗事件 pending 加入、resolved 移除；刷新后由状态轮询恢复
  const [approvals, setApprovals] = useState<Approval[]>([])
  // 会话（/new 开新对话、可切回；展示当前会话 id + 列表）
  const [currentSession, setCurrentSession] = useState<string>('default')
  const [sessionList, setSessionList] = useState<{ id: string; title?: string; last_active_at?: string; pinned?: boolean }[]>([])
  const currentSessionRef = useRef(currentSession)
  currentSessionRef.current = currentSession
  // 供 WS 回调（onMessage 闭包）引用组件级滚动函数——[INSERT] 插入消息后滚到底部
  /**
   * 贴底跟随：唯一实现见 hooks/useStickToBottom（DSH 对话页用同一份）。
   * 解构成旧名字，是为了让下面既有的调用点不用动；onReachTop 走 ref，
   * 因为 loadOlder 定义在后面（转发后运行时才取，避免「先用后声明」）。
   */
  const loadOlderFnRef = useRef<() => void>(() => {})
  const stick = useStickToBottom({
    onReachTop: () => loadOlderFnRef.current(),
    onAtBottom: () => { unreadCountRef.current = 0; setUnreadCount(0) },
  })
  const {
    listRef, listEls, eachList, firstList, isAtBottom, isAtBottomRef, canScroll: chatCanScroll,
    scrollToBottom, forceScrollToBottom, follow, resetFollow, measure,
  } = stick
  const sessionListRef = useRef(sessionList)
  sessionListRef.current = sessionList
  const chatProcessingRef = useRef(CHAT_PROCESSING_INITIAL)
  // ref 是 state 的镜像（供状态检查闭包读最新值）——只经此入口写入。
  // 两处分别赋值必然漂移：初始值一度不一致，导致 else 分支永不执行 → 前端永久"处理中"
  const applyProcessing = useCallback((v: boolean) => {
    chatProcessingRef.current = v
    setChatProcessing(v)
  }, [])
  const [chatHasMore, setChatHasMore] = useState(false)
  const [chatLoadingOlder, setChatLoadingOlder] = useState(false)
  const msgSeqRef = useRef(0)  // 本地临时消息 id（负数，避免与 DB id 碰撞）
  const chatInputRef = useRef<HTMLTextAreaElement>(null)
  // 工具执行完 → 世界文件可能被改 → 节流刷新（文件树/世界信息动态更新，不打断聊天流）
  const refreshTimerRef = useRef<number | null>(null)
  const onRefreshRef = useRef(onRefresh)
  onRefreshRef.current = onRefresh
  const [unreadCount, setUnreadCount] = useState(0)
  const unreadCountRef = useRef(0)
  const loadingHistoryRef = useRef(false)  // 标记是否在加载历史（prepend），不增加未读

  // 斜杠命令列表（输入 / 弹出）
  const [cmdActive, setCmdActive] = useState(false)
  const [cmdQuery, setCmdQuery] = useState('')
  const [cmdIdx, setCmdIdx] = useState(0)
  // 命令目录：以后端 COMMAND_SPECS 为准（loadChat 随历史下发），未下发前用本地兜底
  const [worldCommands, setWorldCommands] = useState<CmdSpec[]>(WORLD_COMMANDS)
  const cmdFiltered = useMemo(() =>
    cmdQuery ? worldCommands.filter((c) => c.cmd.startsWith('/' + cmdQuery)) : worldCommands
  , [cmdQuery, worldCommands])
  // 该条能否在 AI 工具轮进行中直接插入本轮——与后端 world_chat_commands.may_insert_mid_turn 同一规则：
  // 普通消息恒可；命令看后端声明的 mid_turn；未声明/未知命令保守按"必须等本轮结束"
  // 注意：这是**路由判定**，与弹窗里的 kind（'cmd'/'msg'，纯显示标签）是两回事
  const mayInsertMidTurn = useCallback((text: string) => {
    const head = (text.trim().split(/\s+/) || [''])[0]
    if (!head.startsWith('/')) return true
    return !!worldCommands.find((c) => (c.cmd.split(/\s+/)[0] || '') === head)?.mid_turn
  }, [worldCommands])

  // ── 历史加载 ──
  const loadChat = useCallback(async (opts?: { before_id?: number; append?: boolean }) => {
    try {
      const q = opts?.before_id ? `?before_id=${opts.before_id}&limit=30` : '?limit=30'
      // 翻页时记录原滚动位置（prepend 后补回，作用于所有面板实例）
      const heights = listEls().map((el) => el.scrollHeight)
      const r = await api.get<{ messages: ChatMsg[]; has_more: boolean; current_session?: string; sessions?: { id: string; title?: string; last_active_at?: string; pinned?: boolean }[]; commands?: CmdSpec[] }>(`/worlds/${wid}/chat${q}`)
      if (r.current_session) setCurrentSession(r.current_session)
      if (Array.isArray(r.sessions)) setSessionList(r.sessions)
      // 命令目录以后端为准（唯一来源）；空数组不下发时保留本地兜底
      if (Array.isArray(r.commands) && r.commands.length) setWorldCommands(r.commands)
      if (opts?.append && opts.before_id) {
        setChatMsgs((msgs) => [...(r.messages || []), ...msgs])
        requestAnimationFrame(() => {
          eachList((el, i) => { el.scrollTop = el.scrollHeight - (heights[i] ?? 0) })
        })
      } else {
        setChatMsgs(r.messages || [])
        // 在底部（跟随模式）才滚到消息末尾；用户往上翻时不打扰
        // ⚠️ 2026-08-13 修复：实时读位置（isAtBottomRef 由 rAF 节流更新可能延迟——
        // 用户刚往上翻时 ref 还是 true，工具 done 后 loadChat 误滚到底部）
        //
        // 注意：这里**可以**直接量位置，而上面那个跟随 effect **不可以**——
        // 区别在时机：本处运行在 setChatMsgs 之后、React 提交之前，量到的还是
        // 更新前的 DOM（= 用户此刻的真实位置）；跟随 effect 跑在提交之后，
        // 内容已撑高而 scrollTop 未跟上，量出来必然是"不在底部"。别把两者"统一"了。
        const el = firstList()
        const atBottomNow = el ? el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD : false
        if (atBottomNow) {
          forceScrollToBottom()
        }
      }
      setChatHasMore(!!r.has_more)
    } catch { /* 历史拉不到不阻塞 */ }
  }, [wid])

  useEffect(() => { loadChat() }, [loadChat])

  // 挂载时拉取建议（持久化的 AI 建议；无历史 → 预设随机 4；有历史无存储 → 空等 AI 回复）
  useEffect(() => {
    if (!wid) return
    let cancelled = false
    api.get<{ suggestions: string[] }>(`/worlds/${wid}/chat/suggest`)
      .then((r) => { if (!cancelled && Array.isArray(r?.suggestions)) setSuggestions(r.suggestions) })
      .catch(() => { /* 失败静默 */ })
    return () => { cancelled = true }
  }, [wid])

  // 滚到顶 → 加载更早消息（主聊天同款无限滚动）
  const loadOlder = useCallback(async () => {
    if (chatLoadingOlder || !chatHasMore || chatMsgs.length === 0) return
    const oldest = chatMsgs[0].id
    // 历史消息来自 DB，id 为正数；本地临时负数消息跳过
    if (!oldest || oldest < 0) return
    setChatLoadingOlder(true)
    loadingHistoryRef.current = true  // 标记是历史加载，不增加未读
    try {
      await loadChat({ before_id: oldest, append: true })
    } finally {
      loadingHistoryRef.current = false
      setChatLoadingOlder(false)
    }
  }, [chatLoadingOlder, chatHasMore, chatMsgs, loadChat])

  loadOlderFnRef.current = loadOlder

  // 消息/布局变化后重新测量可滚性（列表不可滚动时按钮仍显示入口）
  // ⚠️ 依赖 chatMsgs.length 而非 chatMsgs：气泡内容流式更新（每 token 一次）不触发测量，
  // 否则流式输出期间高频读取 scrollHeight/clientHeight 强制 reflow，底部滑动时卡顿
  useEffect(() => { measure() }, [chatMsgs.length, measure])

  // 新消息到达时，如果不在底部，增加未读计数（历史加载时不增加）
  // ⚠️ 2026-08-13 修复：原来依赖 chatMsgs 变化——流式每 chunk 更新都触发（思考气泡逐字、
  // 正文逐字），一秒加几十个未读。改为只在「完整 AI 回复」或「插入消息」到达时计数一次。
  // 实际计数点：subscribeTurnStream 收到 [DONE] 后（完整一轮）+
  // 轮询恢复发现新消息时（check 兜底）——见下方计数组件。
  const countUnreadIfAway = useCallback(() => {
    if (loadingHistoryRef.current) return
    if (!isAtBottomRef.current && chatMsgs.length > 0) {
      unreadCountRef.current += 1
      setUnreadCount(unreadCountRef.current)
    }
  }, [chatMsgs])



  // 新消息 / 流式内容到达 → 在底部才跟随（判定、首次落底、断开跟随都在共享 hook 里）
  // 发送中用瞬时滚，避免 smooth 动画堆积打架
  useEffect(() => { follow(!chatSending) }, [chatMsgs, chatSending, follow])

  // ── 订阅 turn 直播（SSE）：发消息后 / 刷新恢复 共用 ──
  // 断开自动重连（最多 2 次）；返回是否收到 [DONE]（false = 连接失败/重连耗尽，调用方拉历史收尾）
  const runTurnStream = useCallback(async (turnId: string): Promise<boolean> => {
    let full = ''
    let reasoning = ''
    // 2026-08-13：正文/思考独立气泡（顺序递增 id）——思考一个气泡、正文一个气泡，
    // 没有正文就没有正文气泡（不再用占位）；[TOOL_UPDATE] 后都封存开新
    let contentTargetId: number | null = null
    let reasoningTargetId: number | null = null
    const base = (localStorage.getItem('instance_url') || '').replace(/\/+$/, '') + '/api'
    const authHeaders = {
      'Authorization': `Bearer ${localStorage.getItem('access_token')}`,
      'Content-Type': 'application/json',
    }
    let gotDone = false
    for (let attempt = 0; attempt < 2 && !gotDone; attempt++) {
      const streamResp = await fetch(`${base}/worlds/${wid}/chat/stream?turn_id=${turnId}`, { headers: authHeaders })
      if (!streamResp.ok || !streamResp.body) return false
      const reader = streamResp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      // 流式气泡：函数式更新（存在则更新、不存在则创建），同一 id 永不重复——
      // ⚠️ 根本修复：React 18 setState 异步提交，气泡创建未提交时 msgs.map 找不到 id → 更新被吞 → 气泡永远空。
      // 用函数式 setState：无论提交时序，最终 state 里气泡必带最新内容（创建即带内容，更新不丢）。
      const updateBubble = (id: number, patch: Partial<ChatMsg>) => {
        setChatMsgs((msgs) => {
          const idx = msgs.findIndex((m) => m.id === id)
          if (idx >= 0) {
            const next = msgs.slice()
            next[idx] = { ...next[idx], ...patch }
            return next
          }
          return [...msgs, { id, role: 'ai' as const, content: '', ...patch }]
        })
      }
      // 更新思考（2026-08-13 简化：不维护 preview——折叠截断在渲染层做，一个对象）
      const updateBubbleReasoning = (id: number, reasoningText: string) => {
        updateBubble(id, { reasoning: reasoningText })
      }
      // 正文气泡（首次正文到达时创建；id 顺序递增保证时间线）
      const ensureContentBubble = () => {
        if (contentTargetId !== null) return
        contentTargetId = -(++msgSeqRef.current)
        updateBubble(contentTargetId, { content: full })
      }
      // 思考气泡（首次思考到达时创建；独立 id）
      const ensureReasoningBubble = () => {
        if (reasoningTargetId !== null) return
        reasoningTargetId = -(++msgSeqRef.current)
        updateBubble(reasoningTargetId, { reasoning })
      }
      // rAF 节流渲染（每帧最多一次函数式更新）
      let renderPending = false
      const scheduleRender = () => {
        if (renderPending) return
        renderPending = true
        requestAnimationFrame(() => {
          renderPending = false
          if (contentTargetId !== null) updateBubble(contentTargetId, { content: full })
          if (reasoningTargetId !== null) updateBubbleReasoning(reasoningTargetId, reasoning)
        })
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()!
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6)
          if (payload === '[DONE]') {
            gotDone = true
            // 完整一轮结束：若用户不在底部，未读 +1（不是每 chunk 都加）
            window.setTimeout(() => countUnreadIfAway(), 0)
            break
          }
          if (payload.startsWith('[SUGGEST]')) {
            try { setSuggestions(JSON.parse(payload.slice(9))) } catch { /* ignore */ }
            continue
          }
          if (payload.startsWith(EV.INSERTED)) {
            // 信号（不计入历史）：后端已把 count 条普通消息注入 AI 上下文
            // → 按 FIFO 从排队弹窗移除这些消息（设计 §7.7）
            // 清除依据是"这条当初是否走了插入通道"（mayInsertMidTurn），而不是 kind——
            // kind 只是弹窗显示标签（'cmd' 显示成"命令"），与路由无关。
            // 未走插入通道的命令必须留着，否则 drain effect 会漏发。
            //
            // ⚠️ 计数必须定义在 updater **内部**：React StrictMode 会把 updater 调用两次
            //（用来暴露不纯的 updater）。若在外部闭包变量上累减，第二次调用会看到"已经扣完"的
            // 状态而原样返回未过滤的列表，而 React 恰好采用第二次的结果 → 弹窗条目永远清不掉。
            const sig = parseEvent<{ count?: number }>(payload, EV.INSERTED)
            if (sig) {
              const quota = sig.count || 0
              setPendingItems((items) => {
                let left = quota
                return items.filter((it) => {
                  if (left > 0 && mayInsertMidTurn(it.text)) { left -= 1; return false }
                  return true
                })
              })
            }
            continue
          }
          if (payload.startsWith(EV.INSERT)) {
            // 排队消息已插入工具轮并落库（记入历史）：画用户气泡（用真实 msg_id，
            // 与历史一致，loadChat 后不会重复/错位）
            const ins = parseEvent<{ msg_id: number; content: string; attachments?: ReadyAttachment[] }>(payload, EV.INSERT)
            if (ins) {
              setChatMsgs((msgs) => {
                // 正常路径：中途发送只挂排队弹窗、不画气泡，这里直接追加真实气泡。
                // 兜底分支：前端以为空闲（chatProcessing 尚未刷新）→ 画了气泡并走 sendMessages，
                // 后端其实仍忙 → 该 POST 被插进队列 → 此处若不替换就会重复显示，故按内容
                // 匹配最早一条临时气泡原地换 id（后端 drain_inserts 是 FIFO，顺序一致）
                const i = msgs.findIndex((m) => m.id < 0 && m.role === 'user' && m.content === ins.content)
                if (i === -1) return [...msgs, { id: ins.msg_id, role: 'user', content: ins.content, attachments: ins.attachments }]
                const next = [...msgs]
                next[i] = { id: ins.msg_id, role: 'user', content: ins.content, attachments: ins.attachments }
                return next
              })
              requestAnimationFrame(() => forceScrollToBottom())
            }
            continue
          }
          if (payload.startsWith(EV.APPROVAL)) {
            // 审批弹窗：pending 入队、resolved 关闭（同一 approval_id 幂等）
            const ap = parseEvent<Approval & { status: string }>(payload, EV.APPROVAL)
            if (ap) {
              setApprovals((list) => ap.status === 'resolved'
                ? list.filter((a) => a.approval_id !== ap.approval_id)
                : list.some((a) => a.approval_id === ap.approval_id)
                  ? list
                  : [...list, {
                    approval_id: ap.approval_id, kind: ap.kind, title: ap.title, detail: ap.detail,
                    body: ap.body, body_format: ap.body_format, body_lang: ap.body_lang,
                    expires_in: ap.expires_in,
                  }])
            }
            continue
          }
          if (payload.startsWith('[ERROR]')) throw new Error(payload.slice(7))
          if (payload.startsWith('[TOOL_UPDATE]')) {
            // 工具状态事件（2026-08-13：同 tool_id 多状态更新）——
            // running（正在执行 XX）→ update（进度）→ done（完成，同 id 原地更新气泡）
            try {
              const tu = JSON.parse(payload.slice(13))
              const tId = tu.tool_id
              // 先把当前正文/思考气泡同步封存（函数式更新：存在则更新；即使创建未提交也会带内容创建，不丢）
              if (contentTargetId !== null) updateBubble(contentTargetId, { content: full })
              if (reasoningTargetId !== null) updateBubbleReasoning(reasoningTargetId, reasoning)
              // 后续内容开新气泡；full/reasoning 重置避免拼接
              contentTargetId = null
              reasoningTargetId = null
              full = ''
              reasoning = ''
              renderPending = false
              if (tId) {
                // 按 tool_id 定位：有则更新（status 变化原地替换），无则创建
                const base = {
                  role: 'tool' as const, tool_id: tId,
                  tool_name: tu.name, tool_label: tu.label, tool_status: tu.status as any,
                  tool_args: tu.args_summary, tool_detail: tu.detail,
                  error: tu.status === 'done' ? !tu.success : false,
                }
                setChatMsgs((msgs) => {
                  const idx = msgs.findIndex((m) => m.role === 'tool' && m.tool_id === tId)
                  if (idx >= 0) {
                    const next = msgs.slice()
                    next[idx] = { ...next[idx], ...base, content: tu.summary || next[idx].content }
                    return next
                  }
                  return [...msgs, { id: -(++msgSeqRef.current), ...base, content: tu.summary || '' }]
                })
              } else {
                // 无 tool_id（旧格式兜底）：独立气泡
                setChatMsgs((msgs) => [...msgs, { id: -(++msgSeqRef.current), role: 'tool', content: tu.summary || tu.name, error: tu.status === 'done' ? !tu.success : false }])
              }
              // 工具完成 → 世界文件/配置可能被改 → 节流刷新（文件树动态更新；400ms 内多个工具合并一次）
              if (tu.status === 'done') {
                if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
                refreshTimerRef.current = window.setTimeout(() => onRefreshRef.current(), 400)
              }
            } catch { /* 解析失败忽略 */ }
            continue
          }
          if (payload.startsWith('[REASONING]')) {
            reasoning += payload.slice(11).replace(/\{NL\}/g, '\n')
            ensureReasoningBubble()
          } else if (payload.startsWith('[TOOL]')) {
            // 旧格式工具事件（无 _UPDATE）已废弃：忽略，不当作正文显示（2026-08-13）
          } else {
            full += payload.replace(/\{NL\}/g, '\n')
            ensureContentBubble()
          }
          scheduleRender()
        }
        if (gotDone) break
      }
      if (gotDone) break
      // 连接断开且未完成：稍等重连（间隔 1s/2s）
      await new Promise((res) => setTimeout(res, 1000 * (attempt + 1)))
    }
    return gotDone
  }, [wid])

  // 同一个 turn 只允许一条直播：发送路径（sendMessages）与状态轮询恢复（check）都会订阅
  // 同一个 turn_id。各自订阅会各自累积 full/contentTargetId → 跟踪过程中出现两条一样的消息，
  // 整轮结束 loadChat 用权威历史覆盖后才正常（2026-09-18 用户报）。
  // 按 turn_id 单飞，与 DSH 按 rpcId 去重是同一个思路：后来的调用复用在跑的那条流。
  const inflightTurnsRef = useRef(new Map<string, Promise<boolean>>())
  const subscribeTurnStream = useCallback((turnId: string): Promise<boolean> => {
    const inflight = inflightTurnsRef.current
    const existing = inflight.get(turnId)
    if (existing) return existing
    const run = runTurnStream(turnId)
    inflight.set(turnId, run)
    const forget = () => { if (inflight.get(turnId) === run) inflight.delete(turnId) }
    run.then(forget, forget)
    return run
  }, [runTurnStream])

  // 刷新后恢复「思考中」状态：world_turn 在服务器端继续执行，前端状态丢失后订阅直播 + 轮询恢复
  useEffect(() => {
    if (!wid) return
    let timer: number | undefined
    let cancelled = false
    const check = async () => {
      try {
        const base = (localStorage.getItem('instance_url') || '').replace(/\/+$/, '') + '/api'
        const r = await fetch(`${base}/worlds/${wid}/chat/status`, {
          headers: { 'Authorization': `Bearer ${localStorage.getItem('access_token')}` },
        })
        const s = await r.json()
        // 刷新页面后重画待审批弹窗（弹窗事件是一次性广播，错过就靠这里补）
        if (Array.isArray(s?.approvals)) setApprovals(s.approvals)
        if (s && s.processing) {
          applyProcessing(true)
          // 有进行中的 turn → 订阅 SSE 直播，实时看到流式内容（不用等整轮跑完才一次性更新）
          if (s.turn_id && !cancelled) {
            try {
              const done = await subscribeTurnStream(s.turn_id)
              if (cancelled) return
              if (done) {
                // 直播正常结束：拉权威历史收尾 + 刷新用量（缓存命中率）
                applyProcessing(false)
                loadChat()
                onRefreshRef.current()
                return
              }
            } catch { /* 直播失败/中断：继续轮询兜底 */ }
          }
          timer = window.setTimeout(check, 4000)
        } else {
          if (chatProcessingRef.current) {
            applyProcessing(false)
            loadChat()  // 处理完成：拉最新历史（含 AI 回复）
            onRefreshRef.current()  // 刷新用量（缓存命中率）——普通对话也要更新，不只工具场景
          }
          timer = window.setTimeout(check, IDLE_RECHECK_MS)   // 空闲慢轮询：接住外部发起的轮次
        }
      } catch { /* 失败静默重试 */ if (!cancelled) timer = window.setTimeout(check, 8000) }
    }
    // 重新检查前先回到"处理中"：无活跃 turn 时 else 分支据此收敛为 false
    applyProcessing(true)
    check()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [wid, loadChat, subscribeTurnStream, applyProcessing])

  // 卸载清理（节流刷新定时器）
  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    }
  }, [])

  // ── 会话切换 / 收藏（/new /use /pin 的前端等价操作，走 API 不占对话轮次）──
  const switchSession = useCallback(async (sid: string): Promise<boolean> => {
    try {
      const r = await api.post<{ messages: ChatMsg[]; current_session: string; sessions: { id: string; title?: string; last_active_at?: string; pinned?: boolean }[] }>(
        `/worlds/${wid}/chat/session`, { session_id: sid },
      )
      setCurrentSession(r.current_session)
      if (Array.isArray(r.sessions)) setSessionList(r.sessions)
      if (Array.isArray(r.messages)) setChatMsgs(r.messages)
      setChatHasMore(false)
      forceScrollToBottom()
      return true
    } catch { return false }
  }, [wid, forceScrollToBottom])

  /** 新建会话并切过去（走 API，不占对话轮次）。
   *  与"切会话"共用同一返回载荷，AI 正在跑时也能立刻切；
   *  不再像过去那样发一条 /new 聊天消息（那会把 /new 写进旧会话并占一个轮次）。 */
  const newSession = useCallback(async (): Promise<string | null> => {
    try {
      const r = await api.post<{ messages: ChatMsg[]; current_session: string; sessions: { id: string; title?: string; created_at?: string; last_active_at?: string; pinned?: boolean }[] }>(
        `/worlds/${wid}/chat/session/new`, {},
      )
      setCurrentSession(r.current_session)
      if (Array.isArray(r.sessions)) setSessionList(r.sessions)
      setChatMsgs(Array.isArray(r.messages) ? r.messages : [])
      setChatHasMore(false)
      setSuggestions([])
      forceScrollToBottom()
      return r.current_session
    } catch { return null }
  }, [wid, forceScrollToBottom])

  const togglePin = useCallback(async (): Promise<boolean> => {
    try {
      const cur = currentSessionRef.current
      const isPinned = sessionListRef.current.find((s) => s.id === cur)?.pinned
      const r = await api.post<{ pinned: boolean; count: number }>(`/worlds/${wid}/chat/session/pin`, { pin: !isPinned })
      setSessionList((prev) => prev.map((s) => s.id === cur ? { ...s, pinned: r.pinned } : s))
      return r.pinned
    } catch { return false }
  }, [wid])

  /** 会话改名：清洗与存储都在后端一处（set_session_title），前端只传原话 */
  const renameSession = useCallback(async (sessionId: string, title: string) => {
    const r = await api.put<{ session_id: string; title: string | null }>(
      `/worlds/${wid}/chat/session/title`, { session_id: sessionId, title })
    setSessionList((list) => list.map((s) => s.id === r.session_id ? { ...s, title: r.title || undefined } : s))
    return r.title || ''
  }, [wid])

  /** 下载会话记录：Markdown / JSON（后端只导出这场对话本身，与面板所见一致） */
  const exportSession = useCallback(async (sessionId: string, format: 'md' | 'json', fallbackName: string) => {
    await api.download(`/worlds/${wid}/chat/export?session_id=${encodeURIComponent(sessionId)}&format=${format}`, fallbackName)
  }, [wid])

  /** 审批弹窗回执：先乐观收起弹窗，再告诉后端（服务端据此恢复被挡住的工具调用）。
   *  note = 用户在输入框里写的理由/补充要求，随点击一起交给 AI（后端并进工具结果的 user_note）。 */
  const resolveApproval = useCallback(async (approvalId: string, approved: boolean, note = '') => {
    setApprovals((list) => list.filter((a) => a.approval_id !== approvalId))
    try {
      await api.post<{ success: boolean }>(`/worlds/${wid}/chat/approval`, {
        approval_id: approvalId, approved, note: note.trim() || undefined,
      })
    } catch (e: any) {
      onMsg(`审批提交失败: ${e?.message || e}`)
    }
  }, [wid, onMsg])

  /** 审批弹窗心跳：用户正在输入框里打字 → 让服务端把「多久没人动」的计时重置。
   *  返回剩余秒数（0 = 这条审批已被处理），弹窗据此把倒计时接着走下去。
   *  它只是续期，绝不代替用户点按钮——门禁「超时一律不放行」的语义不变。 */
  const touchApproval = useCallback(async (approvalId: string): Promise<number> => {
    try {
      const r = await api.post<{ success: boolean; expires_in: number }>(
        `/worlds/${wid}/chat/approval/touch`, { approval_id: approvalId })
      return r.expires_in
    } catch {
      return 0                       // 心跳失败不值得打扰用户：按「没续上」处理，倒计时照走
    }
  }, [wid])

  // ── 插入消息（AI 运行中中途发送的普通消息，不阻塞等整轮结束）──
  // 设计见 docs/group_world/design/group_world_design.md §7.7：
  //   普通消息 → 立即发后端进插入队列 → 后端在下一轮 LLM 调用前注入
  //   → 回 [INSERTED]{count} 清排队弹窗 + [INSERT] 画真实气泡
  // 这里**不预先画占位气泡**：气泡必须等 AI 真正收到才出现
  //（"用户看到已发送" == "AI 已看到"），否则等于"还没被 AI 收到就已经显示成发出去了"
  const sendInsertMessage = async (text: string, attachments?: ReadyAttachment[]) => {
    try {
      await api.post<{ turn_id: string; queued: boolean }>(`/worlds/${wid}/chat`, {
        items: [{ text, attachments: attachments?.length ? attachments : undefined }],
      })
      // 不 await subscribeTurnStream——回执走活跃 turn 的 SSE（[INSERTED]/[INSERT]）
    } catch (e: any) {
      // 发送失败：从排队弹窗摘掉这条，否则 drain 会把它当普通排队消息再发一次
      setPendingItems((items) => {
        const i = items.findIndex((it) => it.kind === 'msg' && it.text === text)
        return i === -1 ? items : [...items.slice(0, i), ...items.slice(i + 1)]
      })
      setChatMsgs((msgs) => [...msgs, { id: -(++msgSeqRef.current), role: 'ai', content: e?.message || '发送失败', error: true }])
    }
  }

  // ── 发送 ──
  const sendMessages = async (outgoing: OutgoingItem[]) => {
    const list = outgoing
      .map((i) => ({ text: i.text.trim(), attachments: i.attachments || [] }))
      .filter((i) => i.text || i.attachments.length)
    if (!list.length) return
    setChatSending(true)
    setChatInput('')
    setCmdActive(false)
    // 斜杠命令：立即给执行中反馈（后端压缩/清空需要时间，等 [TOOL] 正式结果到达后 loadChat 会清掉这个临时气泡）
    const singleCmd = list.length === 1 ? list[0].text : ''
    if (singleCmd.startsWith('/compact') || singleCmd.startsWith('/clear')) {
      setChatMsgs((msgs) => [...msgs, {
        id: -(++msgSeqRef.current), role: 'tool',
        content: singleCmd.startsWith('/compact') ? '⏳ 正在压缩上下文（可能需要一点时间）…' : '⏳ 正在清空上下文…',
      }])
    }

    // 服务器端轮次（不依赖本页面）：入队 → 订阅直播（断开自动重连，逻辑见 subscribeTurnStream）
    try {
      // 1. 入队（返回 turn_id；若前面有消息在跑会排队）
      const r = await api.post<{ turn_id: string; queued: boolean; position: number }>(`/worlds/${wid}/chat`, {
        items: list.map((i) => ({
          text: i.text,
          attachments: i.attachments.length ? i.attachments : undefined,
        })),
      })
      if (r.queued) {
        setChatMsgs((msgs) => [...msgs, { id: -(++msgSeqRef.current), role: 'tool', content: `⏳ 已排队（前面还有 ${r.position} 条在跑）` }])
      }

      // 2. 订阅直播（SSE）；断连自动重连最多 2 次，耗尽后拉权威历史收尾
      await subscribeTurnStream(r.turn_id)
      // 用权威历史收尾（含断开期间漏掉的工具气泡/最终回复）；世界信息可能被工具改过，一并刷新
      await loadChat()
      onRefresh()
    } catch (e: any) {
      // 出错：独立错误气泡（已流式显示的内容保留，不抹掉）
      const errText = e?.message || '未知错误'
      setChatMsgs((msgs) => [...msgs, { id: -(++msgSeqRef.current), role: 'ai', content: errText, error: true }])
      onMsg(`发送失败: ${errText}`)
    } finally {
      setChatSending(false)
    }
  }

  const submitText = (text: string, attachments?: ReadyAttachment[]) => {
    const t = text.trim()
    if (!t && !attachments?.length) return
    const isCmd = t.startsWith('/')
    // AI 忙（本条发送中 / 后台轮次执行中）：一律进排队弹窗——不画占位气泡
    //（位置不对，且会被 loadChat 冲掉），真正插入后（[INSERT] 回执）才进对话流
    if (chatSending || chatProcessing) {
      setPendingItems((items) => [...items, { kind: isCmd ? 'cmd' : 'msg', text: t, attachments }])
      setChatInput('')
      setCmdActive(false)
      setSuggestions([])  // 开始新工作流 → 旧建议隐藏，等新回复生成新的
      // 可中途插入的（普通消息 + 后端声明 mid_turn 的命令）立即发后端进插入队列；
      // 其余命令不能提前发——必须等本轮结束，由 drain effect 一次发一条，剩下的继续排队
      if (mayInsertMidTurn(t)) sendInsertMessage(t, attachments)
      return
    }
    // 空闲状态：直接发送 + 画用户气泡
    setChatMsgs((msgs) => [...msgs, { id: -(++msgSeqRef.current), role: 'user', content: t, attachments }])
    setSuggestions([])
    sendMessages([{ text: t, attachments }])
  }

  // 插入建议到输入框（追加不覆盖）；输入框 ref 在聊天面板 textarea 上
  const insertSuggestion = (q: string) => {
    setChatInput((prev) => (prev ? prev + ' ' + q : q))
    requestAnimationFrame(() => {
      chatInputRef.current?.focus()
      const ta = chatInputRef.current
      if (ta) {
        const pos = ta.value.length
        ta.setSelectionRange(pos, pos)
      }
    })
  }

  // 流结束后按队列顺序自动处理：连续普通消息一批（一次 API 一起发，逐条气泡）；命令单独（等前一个完成再下一个）
  useEffect(() => {
    if (chatSending || chatProcessing || pendingItems.length === 0) return
    const items = pendingItems
    const firstCmd = items.findIndex((i) => i.kind === 'cmd')
    if (firstCmd === 0) {
      setPendingItems(items.slice(1))
      sendMessages([{ text: items[0].text, attachments: items[0].attachments }])
    } else {
      const n = firstCmd === -1 ? items.length : firstCmd
      setPendingItems(items.slice(n))
      sendMessages(items.slice(0, n).map((i) => ({ text: i.text, attachments: i.attachments })))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatSending, chatProcessing, pendingItems])

  return {
    wid,
    chatMsgs, chatInput, setChatInput, setChatMsgs,
    chatSending, chatProcessing, chatHasMore, chatLoadingOlder,
    chatListRef: listRef, chatInputRef, pendingItems, setPendingItems, suggestions,
    cmdActive, setCmdActive, cmdQuery, setCmdQuery, cmdIdx, setCmdIdx, cmdFiltered, worldCommands,
    submitText, insertSuggestion, isAtBottom, chatCanScroll, scrollToBottom, forceScrollToBottom,
    currentSession, sessionList, switchSession, newSession, togglePin, unreadCount,
    renameSession, exportSession,
    approvals, resolveApproval, touchApproval,
  }
}

// vite-transform-cache-bump: 2026-08-10 14:16
