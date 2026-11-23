import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { useWebSocket, type WebSocketMessage } from '../hooks/useWebSocket'
import { api } from '../api/client'
import { firstImageAttachment } from '../utils/mime'
import { useAuth } from '../context/AuthContext'
import { parseServerDate } from '../utils/time'
import MessageBubble from './MessageBubble'
import { renderMentions, type MentionNames } from '../utils/mentions'
import ChatInput, { CHAT_INPUT_MIN_H } from './ChatInput'
import QuotedMessage from './shared/QuotedMessage'
import NoticeStack, { NOTICE_H, type NoticeItem } from './shared/NoticeStack'
// 阈值（距底多少算「在底部」）与群视界/DSH 对话同一份来源；本组件有自己的虚拟列表机器，不套整个 hook
import { BOTTOM_THRESHOLD } from '../hooks/useStickToBottom'
// 「我正在读这个会话」的唯一真相（侧边栏据此不画红数泡，本组件据此把已读落回后端）
import { conversationKey, setFollowingConversation, useReadingKey } from '../hooks/useReadingConversation'
import { markConversationRead, refreshChatLists } from '../hooks/useChatLists'
import ActivityBar, { type ActivityUser } from './ActivityBar'
import MobileScrollbar from './MobileScrollbar'
import ProfileCard from './ProfileCard'
import { EmptyState } from './ui'
import { Loader2, AlertTriangle, X, ArrowDown, ArrowUp, MessageSquare, Settings, Gamepad2, Globe } from 'lucide-react'
import { CHAT_REFRESH_EVENT } from '../constants'
import { useT } from '../i18n/I18nContext'
import { tryOpenWorldWindow } from '../utils/worldView'
import {  onKeyboardChange } from '../utils/tauri'
import { scrollToInContainer } from '../utils/scroll'
import { useAttachmentUpload } from '../hooks/useAttachmentUpload'
import { AttachmentChips, DropMask } from './AttachmentChips'

// ── 虚拟列表：消息高度估算（纯函数，窗口化渲染用）──
// 只估不量：渲染完不回写真实高度，滚动位置全靠它 + overscan 兜。
// 已知误差来源：图片/附件（一律按 88px 一个算，实际随原图比例变）、长中文（按 28 字符一行，
// 而中文一行只放得下 14~18 字 → 行数偏低）、Markdown 表格与代码块。
// 所以这是像素近似、不是精确布局：误差由 VIRTUAL_OVERSCAN / OVERSCAN_PX 消化，
// 短对话无感，上千条或图文混排时允许有轻微漂移。要做到精确得渲染后回填真实高度。
const estimateMessageHeight = (msg: any, replyHasImage = false): number => {
  const base = 44                        // 气泡 + 间距基础
  const text = msg.content || ''
  const lines = Math.max(1, Math.ceil(text.length / 28))  // 每行约 28 字符
  const textH = lines * 20
  const attachH = (msg.attachments?.length || 0) * 88     // 附件/图片
  // 语音下面还挂着文稿（最多 2 行）：按 22 字一行估，每行 16px
  const transcriptH = (msg.attachments || []).reduce((h: number, a: any) => {
    const t = typeof a?.transcript === 'string' ? a.transcript.trim() : ''
    return h + (t ? Math.min(2, Math.ceil(t.length / 22)) * 16 : 0)
  }, 0)
  // 回复引用：被引的是一张图时，引用块里多一张 36px 缩略图（+边框/间距 ≈ 44）
  const replyH = msg.reply_to ? (replyHasImage ? 44 : 26) : 0
  return base + textH + attachH + replyH + transcriptH
}

const VIRTUAL_OVERSCAN = 40   // 窗口外预渲染条数（估算误差缓冲）
const OVERSCAN_PX = 1200      // 视口上下各多渲染的像素

// 撤回窗口（与后端一致：2 分钟内、只能撤自己的）；这里只是别让人点了必然失败
const REVOKE_WINDOW_MS = 2 * 60 * 1000

// 「正在录音」的兜底清除：TTS 走的是外部服务，实测一次合成能到两分多钟。
// 给成一个"彻底没下文"的宽限（收到新的 speaking 事件会重新计时），
// 而不是一个"正常语速"的估计值——估计值小了，界面会在合成中途自己退回「思考中」
const SPEAKING_STALE_MS = 5 * 60 * 1000

interface Message {
  id: number
  group_id?: number
  session_id?: string
  sender_type: string
  sender_id: number
  sender_name: string | null
  sender_avatar_url?: string | null
  content: string
  reply_to: number | null
  read_at?: string | null
  attachments?: Array<{file_id?: number, name?: string, size?: number, mime_type?: string, type?: string, invitation_id?: number, group_name?: string, inviter_name?: string, status?: string}> | null
  source_public_id?: string | null
  via?: string | null
  message_type?: string
  sender_state?: string | null
  /** 被撤回了：正文不再显示（后端只回 revoked 标记，不给原文） */
  revoked?: boolean
  created_at: string
}

interface ChatViewProps {
  conversationType: 'group' | 'dm'
  conversationId: number | string
  /** 我在这个群里的角色（owner/admin 才能撤别人的消息；私信没有这个） */
  myRole?: string
  /** 群聊面板容器：世界门要盖住整个面板（含面板顶部标题栏），本组件自己的 absolute 只到消息区 */
  overlayHost?: HTMLElement | null
}

const PAGE_SIZE = 20
// 一次补齐最多拉多少页：离开一天也就几百条；再多交给触底继续拉，别把首屏拖住
const CATCH_UP_MAX_PAGES = 50
// 首屏「首条未读」只有落在最新这么多条里才自动把视口拉过去。
// 再早的未读不抢视口：刷新后被拽回一周前、中间还补不全的观感就是这么来的
const AUTO_LOCATE_RECENT = 10

// ── 模块级工具函数 ──

/** 检查 WebSocket 消息是否属于当前对话 */
function isMessageForThisConversation(
  data: { group_id?: number; session_id?: string },
  convType: 'group' | 'dm',
  convId: number | string,
): boolean {
  return (convType === 'group' && (data as any).group_id === convId) ||
         (convType === 'dm' && (data as any).session_id === convId)
}

/**
 * 上下哨兵：滚到边界就去加载下一页。
 *
 * 游标走 ref（oldestIdRef / newestIdRef）而不是 messages：observer 只在 hasMore / loadingState /
 * 方向变化时重建，不必每来一条消息就重新订阅一次。
 */
function useSentinel({
  containerRef, sentinelRef, cursorRef, hasMore, direction, loadingState, loadMessages,
}: {
  containerRef: React.RefObject<HTMLDivElement | null>
  sentinelRef: React.RefObject<HTMLDivElement | null>
  cursorRef: React.RefObject<number | null>
  hasMore: boolean
  direction: 'older' | 'newer'
  loadingState: 'initial' | 'older' | 'newer' | null
  loadMessages: (params: {
    before_id?: number; after_id?: number; mode: 'initial' | 'older' | 'newer'
  }) => Promise<Message[]>
}) {
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingState) {
          const cursorId = cursorRef.current
          if (cursorId) {
            loadMessages(
              direction === 'older'
                ? { before_id: cursorId, mode: 'older' }
                : { after_id: cursorId, mode: 'newer' }
            )
          }
        }
      },
      { root: containerRef.current, threshold: 0.1 }
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasMore, loadingState, direction, loadMessages, containerRef, sentinelRef, cursorRef])
}

/** 从 Map state setter 中移除指定 id */
function removeFromMap<K, V>(
  setter: React.Dispatch<React.SetStateAction<Map<K, V>>>,
  id: K,
) {
  setter((prev) => {
    const next = new Map(prev)
    next.delete(id)
    return next
  })
}

/** 向 Map state setter 中添加指定键值对 */
function addToMap<K, V>(
  setter: React.Dispatch<React.SetStateAction<Map<K, V>>>,
  id: K,
  value: V,
) {
  setter((prev) => {
    const next = new Map(prev)
    next.set(id, value)
    return next
  })
}

export default function ChatView({ conversationType, conversationId, myRole, overlayHost }: ChatViewProps) {
  // ── 群视界：群聊绑定的世界（全屏入口弹窗，先不加载消息） ──
  const [boundWorldId, setBoundWorldId] = useState<number | null>(null)
  const [worldModalOpen, setWorldModalOpen] = useState(false)
  const t = useT()
  const { user } = useAuth()
  const userId = user?.id
  const [messages, setMessages] = useState<Message[]>([])
  const [revokeNotice, setRevokeNotice] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [loadingState, setLoadingState] = useState<'initial' | 'older' | 'newer' | null>(null)
  const [inputHeight, setInputHeight] = useState<number | null>(null)  // 输入框高度（null=自动）
  const inputResizeRef = useRef<boolean>(false)
  const [hasMoreBefore, setHasMoreBefore] = useState(false)
  const [hasMoreAfter, setHasMoreAfter] = useState(false)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [firstUnreadId, setFirstUnreadId] = useState<number | null>(null)
  const [showJumpToUnread, setShowJumpToUnread] = useState(false)
  const [profileCard, setProfileCard] = useState<{
    type: string; id: number; name: string; state?: string
  } | null>(null)
  const [thinkingAgents, setThinkingAgents] = useState<Map<number, { name: string; avatarUrl: string | null }>>(new Map())
  const [typingAgents, setTypingAgents] = useState<Map<number, { name: string; avatarUrl: string | null }>>(new Map())
  // 正在说话（发语音时的 TTS 合成那几秒）：与"输入中"分开一条状态，正文照旧走 typing
  const [speakingAgents, setSpeakingAgents] = useState<Map<number, { name: string; avatarUrl: string | null }>>(new Map())
  // 人类打字状态: userId → {name, avatarUrl}
  const [humanTyping, setHumanTyping] = useState<Map<number, { name: string; avatarUrl: string | null }>>(new Map())
  // 「录音中」的续期计时器：每个 AI 一条。TTS 合成是一次长等待，期间没有别的事件，
  // 所以每收到一次 state=speaking 就重新计时；被思考/落地/轮末收掉时一起清
  const speakingTimers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())
  const dropSpeaking = useCallback((id: number) => {
    const timer = speakingTimers.current.get(id)
    if (timer) { clearTimeout(timer); speakingTimers.current.delete(id) }
    removeFromMap(setSpeakingAgents, id)
  }, [])
  const holdSpeaking = useCallback((id: number, info: { name: string; avatarUrl: string | null }) => {
    addToMap(setSpeakingAgents, id, info)
    const timer = speakingTimers.current.get(id)
    if (timer) clearTimeout(timer)
    speakingTimers.current.set(id, setTimeout(() => {
      speakingTimers.current.delete(id)
      removeFromMap(setSpeakingAgents, id)
    }, SPEAKING_STALE_MS))
  }, [])

  // 合并所有活动用户（AI 思考 + AI 输入中 + 人类打字）
  const activityUsers: ActivityUser[] = useMemo(() => {
    const map = new Map<number, ActivityUser>()

    thinkingAgents.forEach((info, id) => {
      map.set(id, { id, name: info.name, avatarUrl: info.avatarUrl, status: 'thinking' })
    })
    typingAgents.forEach((info, id) => {
      const existing = map.get(id)
      if (existing) existing.status = 'typing'
      else map.set(id, { id, name: info.name, avatarUrl: info.avatarUrl, status: 'typing' })
    })
    // 说话覆盖前两者：它说的是"此刻在出声"，比"在想要说什么"更具体
    speakingAgents.forEach((info, id) => {
      map.set(id, { id, name: info.name, avatarUrl: info.avatarUrl, status: 'speaking' })
    })
    humanTyping.forEach(({ name, avatarUrl }, id) => {
      map.set(id, { id, name, avatarUrl, status: 'typing' })
    })

    return Array.from(map.values())
  }, [thinkingAgents, typingAgents, speakingAgents, humanTyping])

  // 群成员（ChatInput 的 @提及 与成员状态点都用它）
  const [groupMembers, setGroupMembers] = useState<Array<{ type: string; id: number; name: string; state?: string }>>([])
  const [replyTo, setReplyTo] = useState<{ id: number; sender_name: string; content: string } | null>(null)

  // 文件附件：上传逻辑与群视界世界对话共用 useAttachmentUpload
  //（图片如何进 LLM 多模态由后端 app/utils/multimodal.py 统一处理）
  const attachments = useAttachmentUpload()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Refs
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const inputRef = useRef(input)
  inputRef.current = input // 保持同步，供 effect cleanup 闭包读取最新值
  const containerRef = useRef<HTMLDivElement>(null)
  // 输入区浮在消息之上（和手机端底部菜单同一个做法）：它不再占布局空间，
  // 所以要把自己的高度量出来，垫成消息列表的底部留白，最后一条才滚得出来
  const composerRef = useRef<HTMLDivElement>(null)
  // 初值给个近似高度（一行的输入区），首帧就按它留白；挂载后 ResizeObserver 立刻校准，
  // 免得刚打开会话时消息列表先按 0 留白、再跳一下
  const [composerH, setComposerH] = useState(112)
  useEffect(() => {
    const el = composerRef.current
    if (!el) return
    const update = () => setComposerH(el.offsetHeight)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [conversationId, conversationType])

  // ── 虚拟列表状态（窗口化渲染，长对话不卡）──
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportH, setViewportH] = useState(600)
  const topSentinelRef = useRef<HTMLDivElement>(null)
  const bottomSentinelRef = useRef<HTMLDivElement>(null)
  const firstUnreadRef = useRef<HTMLDivElement>(null)
  const prevMessageCount = useRef(0)
  const isAutoScrolling = useRef(false)
  const prevScrollHeight = useRef(0)
  const isAtBottomRef = useRef(true)
  // 贴底状态只有这一个写入口：state 给渲染用，ref 给 rAF 节流之外要同步读的地方用
  //（滚动监听、ws 回调）。两处各写各的会打架——「看着在底部」和「其实翻走了」同时成立
  const setAtBottom = useCallback((value: boolean) => {
    isAtBottomRef.current = value
    setIsAtBottom(value)
  }, [])
  // 会话边界（最早/最新一条的 id）只有一份真相：按 id 排好序的 messages 的首尾。
  // 这两个 ref 只是它的镜像，**只由下面那个同步 effect 写** —— 供哨兵和渲染之外的地方（断线补漏、
  // 卸载时存已读位）同步读取。
  // 以前是每个分页各自改游标：往上翻一页会把 newest 拽成那一页的末条（「会话最新」直接退到一周前），
  // 于是断线补漏从一周前开始补、卸载时 lastRead 也写成一周前——下次进来又照着它定位。
  const newestIdRef = useRef<number | null>(null)
  const oldestIdRef = useRef<number | null>(null)
  const typingRef = useRef(false)                       // 当前是否正在输入中，避免重复发送 typing 消息
  const showJumpToUnreadRef = useRef(false)              // 避免 scroll 中高频 setState

  // messages 是排序后的唯一真相，两端镜像到 ref。分页方向不再自己碰游标。
  useEffect(() => {
    if (messages.length === 0) return
    oldestIdRef.current = messages[0].id
    newestIdRef.current = messages[messages.length - 1].id
  }, [messages])

  // 离开对话时保存已读位置（通过 newestIdRef，无需额外的 messages 同步 effect）
  useEffect(() => {
    return () => {
      if (newestIdRef.current && conversationId) {
        const key = `lastRead_${conversationType}_${conversationId}`
        localStorage.setItem(key, String(newestIdRef.current))
      }
    }
  }, [conversationType, conversationId])

  // 输入框草稿缓存：切换对话时保存旧草稿、恢复新草稿；页面刷新/崩溃后内容不丢
  useEffect(() => {
    if (!conversationId) return
    const draftKey = `draft_${conversationType}_${conversationId}`
    const draft = localStorage.getItem(draftKey)
    setInput(draft || '')
    return () => {
      if (inputRef.current.trim()) {
        localStorage.setItem(draftKey, inputRef.current)
      } else {
        localStorage.removeItem(draftKey)
      }
    }
  }, [conversationType, conversationId])

  // 输入中自动保存草稿（500ms 防抖，防止崩溃/掉线丢失）
  useEffect(() => {
    if (!conversationId) return
    const draftKey = `draft_${conversationType}_${conversationId}`
    const timer = setTimeout(() => {
      if (input.trim()) {
        localStorage.setItem(draftKey, input)
      } else {
        localStorage.removeItem(draftKey)
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [input, conversationType, conversationId])

  // 页面刷新/关闭时同步保存草稿（beforeunload 是同步事件，cleanup 不保证执行）
  useEffect(() => {
    if (!conversationId) return
    const draftKey = `draft_${conversationType}_${conversationId}`
    const handleBeforeUnload = () => {
      if (inputRef.current?.trim()) {
        localStorage.setItem(draftKey, inputRef.current)
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [conversationType, conversationId])

  // 移动端键盘弹出时滚动到输入框位置（visualViewport API，比 setTimeout 更精确）
  useEffect(() => {
    if (window.innerWidth >= 768 || !window.visualViewport) return
    return onKeyboardChange((visible) => {
      if (visible && textareaRef.current) {
        // 键盘弹出 → 等 visualViewport 重排完成后滚动到输入框
        requestAnimationFrame(() => {
          textareaRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
        })
      }
    })
  }, [])

  // 短延时任务是"这一屏的收尾动作"（滚到底、打字状态回退）：切对话时必须一起清掉，
  // 否则 50ms 后那个滚动会把刚进去的新对话也拽到底部，本该停在首个未读处
  const pendingTimers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set())
  const schedule = useCallback((fn: () => void, ms: number) => {
    const id = setTimeout(() => {
      pendingTimers.current.delete(id)
      fn()
    }, ms)
    pendingTimers.current.add(id)
  }, [])

  // 程序化滚动期间滚动监听要闭嘴（否则会被当成"用户滚走了"）
  const markAutoScrolling = useCallback((ms = 500) => {
    isAutoScrolling.current = true
    schedule(() => { isAutoScrolling.current = false }, ms)
  }, [schedule])

  useEffect(() => {
    const timers = pendingTimers.current
    return () => {
      timers.forEach(clearTimeout)
      timers.clear()
    }
  }, [conversationType, conversationId])

  const handleMessage = useCallback((msg: WebSocketMessage) => {
    if (msg.type === 'message') {
      const m = msg.data
      // 过滤：仅处理当前对话的消息（防止 DM 邀请卡闪现到群聊）
      if (!isMessageForThisConversation(m, conversationType, conversationId)) return
      setMessages((prev) => {
        // 去重：防止 WebSocket 与 HTTP fetch 竞态导致同 ID 消息重复
        if (prev.some((existing) => existing.id === m.id)) return prev
        return [...prev, m]
      })
      if (isAtBottomRef.current) {
        schedule(() => scrollToBottom(true), 50)
      }
      // 不做任何列表广播：能收到**所有**会话推送的是常驻通知连接，会话列表由它就地改
      //（见 hooks/useChatLists）。在这里广播一次 = 三个消费者各拉一次列表，白费
      if (m.sender_type === 'ai' && m.sender_id) {
        removeFromMap(setThinkingAgents, m.sender_id)
        removeFromMap(setTypingAgents, m.sender_id)
        dropSpeaking(m.sender_id)
      }
    } else if (msg.type === 'message_revoked') {
      const d = msg.data
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      setMessages((prev) => prev.map((m) => (m.id === d.id ? { ...m, revoked: true, content: '' } : m)))
      // 侧栏那一行预览也得跟着撤：它存的是列表接口给的字符串，不重拉就还挂着原话
      refreshChatLists()
    } else if (msg.type === 'ai_thinking') {
      const d = msg.data
      if (d.trigger === 'auto') return
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      // 新一轮开始了：上一条语音的"正在录音"到这儿就该收
      dropSpeaking(d.user_id)
      addToMap(setThinkingAgents, d.user_id, { name: d.agent_name, avatarUrl: d.agent_avatar_url || null })
    } else if (msg.type === 'ai_thinking_end') {
      const d = msg.data
      if (d.trigger === 'auto') return
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      removeFromMap(setThinkingAgents, d.user_id)
      removeFromMap(setTypingAgents, d.user_id)
      dropSpeaking(d.user_id)
    } else if (msg.type === 'ai_typing') {
      const d = msg.data
      if (d.trigger === 'auto') return
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      const info = { name: d.agent_name, avatarUrl: d.agent_avatar_url || null }
      // 收到 ai_typing 就说明思考结束了：和下面的状态同一批 setState，不会闪；
      // 以前挂 1.5s 定时器，切对话时被清掉就成了永久「思考中」
      removeFromMap(setThinkingAgents, d.user_id)
      if (d.state === 'speaking') {
        holdSpeaking(d.user_id, info)
      } else {
        addToMap(setTypingAgents, d.user_id, info)
      }
    } else if (msg.type === 'typing') {
      // 人类打字状态
      const d = msg.data
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      if (d.is_typing) {
        setHumanTyping(prev => {
          const next = new Map(prev)
          next.set(d.sender_id, { name: d.username, avatarUrl: d.avatar_url || null })
          return next
        })
      } else {
        setHumanTyping(prev => {
          const next = new Map(prev)
          next.delete(d.sender_id)
          return next
        })
      }
    } else if (msg.type === 'announcement') {
      const d = msg.data
      if (d.group_id !== conversationId) return
      setMessages((prev) => [...prev, {
        id: -Date.now(),
        sender_type: 'system',
        sender_id: 0,
        sender_name: t('groupSettings:announcement'),
        content: d.content,
        reply_to: null,
        created_at: new Date().toISOString(),
      } as Message])
    } else if (msg.type === 'avatar_updated') {
      const d = msg as any
      const etype = d.entity_type
      const eid = d.entity_id
      const newUrl = d.avatar_url
      if (etype && eid && newUrl) {
        setMessages((prev) =>
          prev.map((m) =>
            m.sender_type === etype && m.sender_id === eid
              ? { ...m, sender_avatar_url: newUrl }
              : m,
          ),
        )
      }
    } else if (msg.type === 'user_online' || msg.type === 'user_offline') {
      // 在线状态变化 → 通知 ChatArea 更新在线人数
      window.dispatchEvent(new CustomEvent('online-count-change', { detail: msg }))
    } else if (msg.type === 'state_change') {
      // DM 对方状态变化 → 通知 DMChatView 更新头部状态点
      window.dispatchEvent(new CustomEvent('dm-partner-state-change', { detail: msg.data }))
    } else if (msg.type === 'dm_notification' || msg.type === 'unread_update') {
      window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT, { detail: msg }))
    } else if (msg.type === 'maintenance_update') {
      // 全局广播：维护模式状态变化
      window.dispatchEvent(new CustomEvent('ws-maintenance-update', { detail: msg }))
    }
  }, [conversationType, conversationId, t, schedule])

  const { connected, reconnecting, errors, sendMessage, sendTyping, clearErrors } = useWebSocket(
    conversationType, conversationId, { onMessage: handleMessage },
  )

  // 获取上传大小限制（缓存 5 分钟）
  useEffect(() => {
    const cached = sessionStorage.getItem('upload_limits')
    if (cached) {
      const parsed = JSON.parse(cached)
      if (Date.now() - parsed.ts < 300000) return // 缓存有效
    }
    api.get('/user/config/upload-limits').then((limits: any) => {
      sessionStorage.setItem('upload_limits', JSON.stringify({ ...limits, ts: Date.now() }))
    }).catch(() => {})
  }, [])

  // 切换对话 / 卸载时：清除输入中状态，避免对方看到残留的 typing 指示
  useEffect(() => {
    return () => {
      if (typingRef.current) {
        typingRef.current = false
        sendTyping(false)
      }
    }
  }, [conversationType, conversationId, sendTyping])

  // 稳定引用，配合 MessageBubble 的 React.memo 避免输入时重渲染消息列表
  const handleAvatarClick = useCallback((type: string, id: number, name: string, state?: string) => {
    setProfileCard({ type, id, name, state })
  }, [])

  // 消息列表 memo：窗口化渲染（虚拟列表）——只渲染视口附近的消息，
  // 上下用估算高度占位，解决“对话一多就卡”的 DOM 节点爆炸问题
  // 被引用的是张图时引用块里多一张缩略图：高度估算要跟着，否则虚拟列表会把滚动位置算偏
  const replyImageIds = useMemo(
    () => new Set(messages.filter(m => firstImageAttachment(m.attachments)).map(m => m.id)),
    [messages],
  )
  const heights = useMemo(
    () => messages.map(m => estimateMessageHeight(m, m.reply_to != null && replyImageIds.has(m.reply_to))),
    [messages, replyImageIds],
  )
  const cumHeights = useMemo(() => {
    const a: number[] = [0]
    for (const h of heights) a.push(a[a.length - 1] + h)
    return a
  }, [heights])
  const totalHeight = cumHeights[cumHeights.length - 1] || 0

  const windowRange = useMemo(() => {
    if (messages.length === 0) return { s: 0, e: 0 }
    const startPx = Math.max(0, scrollTop - OVERSCAN_PX)
    const endPx = scrollTop + viewportH + OVERSCAN_PX
    let s = 0
    while (s < messages.length && cumHeights[s + 1] <= startPx) s++
    let e = s
    while (e < messages.length && cumHeights[e] < endPx) e++
    e = Math.min(messages.length, e + VIRTUAL_OVERSCAN)
    s = Math.max(0, s - VIRTUAL_OVERSCAN)
    return { s, e }
  }, [scrollTop, viewportH, messages.length, cumHeights])

  // @令牌 <@!id> 要显示成名字：群聊有成员表，私信没有（也就没有 @）
  const mentionNames = useMemo(
    () => Object.fromEntries(groupMembers.map((m) => [m.id, m.name])) as MentionNames,
    [groupMembers]
  )

  // 撤回按钮只在窗口内出现（后端仍会二次校验——这里只是别让人点了必然失败）
  const isGroupAdmin = myRole === 'owner' || myRole === 'admin'
  // 是不是我发的：必须是稳定引用——每渲染新建一个函数会让 messageElements 的 memo 每次都失效；
  // 也必须跟着登录用户走，否则换账号后撤回判定还按旧 user.id
  const isOwnMessage = useCallback(
    (msg: Message) => msg.sender_type === 'human' && msg.sender_id === userId,
    [userId],
  )
  const canRevoke = useCallback(
    (msg: Message) =>
      !msg.revoked
      && (isOwnMessage(msg) || isGroupAdmin)   // 群主/管理员能撤别人的（口径与后端 is_group_admin 一致）
      // 必须按 UTC 解析：后端 DateTime 是 naive UTC，直接 new Date(...) 会当成本地时间
      //（UTC+8 下每条消息都被算成"过了 8 小时"，窗口判定永远不成立——2026-09-26 的教训）
      && Date.now() - parseServerDate(msg.created_at).getTime() < REVOKE_WINDOW_MS,
    [isGroupAdmin, isOwnMessage],
  )

  /** 撤回：服务端说了算（2 分钟内、只能撤自己的）；成功那条会经 WS 广播变成占位 */
  const handleRevoke = useCallback(async (messageId: number) => {
    const base = conversationType === 'dm' ? `/dm/${conversationId}` : `/gm/${conversationId}`
    try {
      const res: any = await api.post(`${base}/messages/${messageId}/revoke`)
      const failed = (res?.channel || []).filter((c: any) => !c.ok)
      if (failed.length) {
        // 通道侧没撤掉要如实说：QQ 只有 2 分钟窗口、还要求机器人有权限（站内已撤，别让人以为全撤了）
        setRevokeNotice(t('chat:revokedChannelFailed', {
          reason: failed.map((c: any) => c.reason).join('；'),
        }))
      }
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, revoked: true, content: '' } : m)))
      // 自己撤回时 WS 不会回到自己（私信广播按 exclude_user_id 排除了发起者），列表得自己刷
      refreshChatLists()
    } catch (e: any) {
      setRevokeNotice(e?.message || String(e))
    }
  }, [conversationType, conversationId, t])

  // 按 id 直取：窗口里每条都从头 find 一遍是 O(窗口 × 全量)，而每次滚动都会重算一遍
  const messageById = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages])

  // 语音文稿按 file_id 取：文稿的家在 file_metadata（消息负载里不带它），
  // 这样转发、ASR 回填都只改一处，消息快照也不会变成 N 份。一次拿齐缺的那些，拿到就缓存。
  const [transcripts, setTranscripts] = useState<Record<number, string>>({})
  const transcriptFetchedRef = useRef<Set<number>>(new Set())
  const transcriptIds = useMemo(() => {
    const ids: number[] = []
    for (const m of messages) {
      for (const a of (m.attachments || []) as Array<{ file_id?: number; mime_type?: string }>) {
        if (a?.file_id && typeof a.mime_type === 'string' && a.mime_type.startsWith('audio/')) {
          ids.push(Number(a.file_id))
        }
      }
    }
    return Array.from(new Set(ids))
  }, [messages])
  useEffect(() => {
    const missing = transcriptIds.filter((id) => !transcriptFetchedRef.current.has(id))
    if (missing.length === 0) return
    missing.forEach((id) => transcriptFetchedRef.current.add(id))
    api.get<Record<string, string>>(`/fs/transcripts?file_ids=${missing.join(',')}`)
      .then((res) => {
        const patch: Record<number, string> = {}
        for (const [k, v] of Object.entries(res || {})) patch[Number(k)] = v
        if (Object.keys(patch).length > 0) setTranscripts((prev) => ({ ...prev, ...patch }))
      })
      .catch(() => { missing.forEach((id) => transcriptFetchedRef.current.delete(id)) })
  }, [transcriptIds])

  const messageElements = useMemo(() => {
    const { s, e } = windowRange
    const items = messages.slice(s, e).map((msg) => {
      const replyToData = msg.reply_to == null ? undefined : (() => {
        const quoted = messageById.get(msg.reply_to)
        if (!quoted) return { id: msg.reply_to, sender: '?', content: '' }
        return {
          id: quoted.id,
          sender: quoted.sender_name || `用户${quoted.sender_id}`,
          content: renderMentions(quoted.content.slice(0, 80), mentionNames),
          // 被引用的是图/图+字：把那张图缩成小图带过来，否则引用只剩一句"[图片]"
          image: firstImageAttachment(quoted.attachments),
        }
      })()
      return (
        <div
          key={msg.id}
          data-message-id={msg.id}
          ref={msg.id === firstUnreadId ? firstUnreadRef : undefined}
        >
          {msg.id === firstUnreadId && hasMoreBefore && (
            <div className="flex items-center gap-2 my-3 select-none">
              <div className="flex-1 h-px bg-rose-500/30" />
              <span className="text-3xs font-medium text-rose-400 whitespace-nowrap">{t('chat:newMessages')}</span>
              <div className="flex-1 h-px bg-rose-500/30" />
            </div>
          )}
          <MessageBubble
            senderName={msg.sender_name || `${msg.sender_type}:${msg.sender_id}`}
            senderAvatarUrl={msg.sender_avatar_url}
            content={renderMentions(msg.content, mentionNames)}
            isMine={isOwnMessage(msg)}
            revoked={msg.revoked}
            onRevoke={canRevoke(msg) ? handleRevoke : undefined}
            createdAt={msg.created_at}
            senderType={msg.sender_type}
            senderId={msg.sender_id}
            state={msg.sender_state ?? undefined}
            sourcePublicId={msg.source_public_id}
            via={msg.via}
            attachments={msg.attachments}
            messageType={msg.message_type}
            messageId={msg.id}
            replyTo={replyToData}
            onAvatarClick={handleAvatarClick}
            onReply={(id, name, text) => setReplyTo({ id, sender_name: name, content: text.slice(0, 80) })}
            transcripts={transcripts}
          />
        </div>
      )
    })
    return {
      beforeH: cumHeights[s],
      afterH: totalHeight - cumHeights[e],
      items,
    }
  }, [windowRange, messages, messageById, cumHeights, totalHeight, firstUnreadId, hasMoreBefore, isOwnMessage, handleAvatarClick, mentionNames, canRevoke, handleRevoke, transcripts, t])

  // ============================================================
  // 消息加载器
  // ============================================================

  const loadMessages = useCallback(async (params: {
    before_id?: number
    after_id?: number
    mode: 'initial' | 'older' | 'newer'
  }) => {
    const { before_id, after_id, mode } = params
    // 避免 initial 模式重复设值（调用方已设过）
    if (mode !== 'initial') setLoadingState(mode)

    // 加载旧消息前记录当前滚动高度
    if (mode === 'older' && containerRef.current) {
      prevScrollHeight.current = containerRef.current.scrollHeight
    }

    try {
      const queryParams = new URLSearchParams()
      queryParams.set('limit', String(PAGE_SIZE))
      if (before_id) queryParams.set('before_id', String(before_id))
      if (after_id) queryParams.set('after_id', String(after_id))

      let fetched: Message[]
      if (conversationType === 'group') {
        fetched = await api.get<Message[]>(
          `/gm/${conversationId}/messages?${queryParams.toString()}`
        )
      } else {
        fetched = await api.get<Message[]>(
          `/dm/${conversationId}/messages?${queryParams.toString()}`
        )
      }

      const gotFullPage = fetched.length >= PAGE_SIZE

      setMessages((prev) => {
        if (mode === 'initial') return fetched
        // 按 id 归并：两个方向都往同一份列表里插，谁在前谁在后由 id 说，不靠拼接顺序。
        // 「往前插 / 往后接」只在列表本身没有洞时成立；补丢消息补的正是洞，那时拼接会把
        // 新页塞到列表末尾（2026-10-04 群 69 那屏：10-03 的消息下面直接接 10-04 深夜的）
        const byId = new Map(prev.map((m) => [m.id, m]))
        for (const m of fetched) byId.set(m.id, m)
        return [...byId.values()].sort((a, b) => a.id - b.id)
      })

      if (mode === 'older') {
        setHasMoreBefore(gotFullPage)
        if (!gotFullPage) setFirstUnreadId(null)
        // 恢复滚动位置：旧消息插入头部后，补偿新增高度
        requestAnimationFrame(() => {
          if (containerRef.current && prevScrollHeight.current > 0) {
            const newHeight = containerRef.current.scrollHeight
            containerRef.current.scrollTop += (newHeight - prevScrollHeight.current)
            prevScrollHeight.current = 0
          }
        })
      } else if (mode === 'newer') {
        setHasMoreAfter(gotFullPage)
      } else {
        // initial
        setHasMoreBefore(gotFullPage)
        setHasMoreAfter(false)

        if (fetched.length > 0) {
          const key = `lastRead_${conversationType}_${conversationId}`
          const stored = localStorage.getItem(key)
          const lastReadId = stored ? parseInt(stored) : null

          if (lastReadId && lastReadId > 0) {
            // 上次已读过的对话：找第一条 > lastReadId 的消息作为"首个未读"
            const firstUnread = fetched.find(m => m.id > lastReadId)
            if (firstUnread) {
              setFirstUnreadId(firstUnread.id)
            }
            // 所有消息都已读 → firstUnreadId 保持 null
          }
          // 没有 lastRead（无痕窗口/新设备）就没有「新消息」分界：不画红线。
          // 以前这里会用本页最旧那条当边界，结果未读数为 0 也横一条「新消息」，
          // 还会把视口往那一页的顶部带
        }
      }
      return fetched
    } catch (err) {
      console.error('加载消息失败:', err)
      return [] as Message[]
    } finally {
      setLoadingState(null)
    }
  }, [conversationId, conversationType])

  // 补齐游标之后丢掉的那段消息
  // 断线/漏推期间缺的那一截：分页只会往前翻，够不着中间（观感："16 分钟前上面直接是 2 天前"）。
  // 从游标处一页页往后拉、按 id 归并，直到不足一页。
  // 返回补齐段的第一条 id（红线画它上面，不是当页最旧那条）；没补到就是 null
  const catchUpFrom = useCallback(async (fromId: number | null): Promise<number | null> => {
    if (!fromId) return null
    let cursor = fromId
    let boundary: number | null = null
    for (let i = 0; i < CATCH_UP_MAX_PAGES; i++) {
      const page = await loadMessages({ after_id: cursor, mode: 'newer' })
      if (page.length > 0 && boundary === null) boundary = page[0].id
      if (page.length < PAGE_SIZE) return boundary
      const last = page[page.length - 1]?.id
      if (!last || last === cursor) return boundary
      cursor = last
    }
    return boundary
  }, [loadMessages])

  // ============================================================
  // 滚动辅助
  // ============================================================

  const scrollToBottom = useCallback((smooth = true) => {
    const el = containerRef.current
    if (!el) return
    markAutoScrolling()
    // 程序化滚动期间滚动监听闭嘴（见 markAutoScrolling），"到没到底"得自己说。
    // 不说的话：点完"回到底部"状态还停在"翻着往上看"，未读就清不掉
    setAtBottom(true)
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'instant' })
  }, [markAutoScrolling, setAtBottom])

  const scrollToMessage = useCallback((messageId: number) => {
    const el = containerRef.current
    if (!el) return
    const idx = messages.findIndex(m => m.id === messageId)
    if (idx >= 0 && cumHeights[idx] !== undefined) {
      // 目标可能在渲染窗口外：先滚到估算位置，窗口更新后再精确定位
      el.scrollTop = cumHeights[idx]
    }
    requestAnimationFrame(() => {
      const msgEl = el.querySelector(`[data-message-id="${messageId}"]`)
      if (msgEl) {
        markAutoScrolling()
        // 只滚动消息容器本身（scrollIntoView 会连带滚动外层 main/Layout，把标题栏滚出视口）
        scrollToInContainer(el, msgEl as HTMLElement, { smooth: true })
      }
    })
  }, [messages, cumHeights, markAutoScrolling])

  const handleJumpToUnread = useCallback(async () => {
    if (!firstUnreadId) return
    // 加载 firstUnreadId 之前的一页消息
    await loadMessages({ before_id: firstUnreadId, mode: 'older' })
    // 滚动到原来的 firstUnreadId 位置。走 schedule：200ms 内切了对话，这次定位
    // 拿的是旧对话的 id 与高度表，作用到新对话上就是把人家滚到随机位置
    schedule(() => scrollToMessage(firstUnreadId), 200)
  }, [firstUnreadId, loadMessages, scrollToMessage, schedule])

  // ============================================================
  // 初始加载
  // ============================================================

  const loadInitialMessages = useCallback(async () => {
    if (!conversationId) return
    if (conversationType === 'group') {
      const membersData = await api.get(`/groups/${conversationId}/members`)
      setGroupMembers(membersData)
    }
    await loadMessages({ mode: 'initial' })
    // 首屏不再自动 catchUpFrom 补中间那段：补到的第一条会被当成「首条未读」定位到视口顶部，
    // 刷新后直接被拽回一周前；补到 CATCH_UP_MAX_PAGES 上限还会留下永久断层——上/下哨兵只在
    // 整页最顶和最底，缝里没人来加载。
    // 现在红线由 loadMessages 按 lastRead 画好（已读位落在这页之前就画本页最旧那条），
    // 想看更早的未读就往上翻（连续分页）或点「跳到首条未读」。
  }, [conversationType, conversationId, loadMessages])

  // Tauri 那条"3 秒窗口没开成 → 整页兜底跳转"的待定标记：用户改选标准界面、切走对话后作废
  const immersivePending = useRef(false)

  // 全屏弹窗：选择「在此标准界面打开」→ 关弹窗 + 加载消息
  const closeWorldModal = useCallback(() => {
    immersivePending.current = false
    setWorldModalOpen(false)
    loadInitialMessages()
  }, [loadInitialMessages])

  // 沉浸界面：独立窗口打开（按世界复用）——桌面 Tauri 用 WebviewWindow，网页用 window.open 新窗口
  // 失败/被拦截 → 回退应用内全屏
  const openImmersive = useCallback(() => {
    if (boundWorldId == null) return
    const url = `/world-view/${boundWorldId}?group_id=${conversationId}`
    if ('__TAURI_INTERNALS__' in window) {
      ;(async () => {
        try {
          const { WebviewWindow } = await import('@tauri-apps/api/webviewWindow')
          const label = `world-immersive-${boundWorldId}`
          const existing = await WebviewWindow.getByLabel(label)
          if (existing) { existing.setFocus(); return }
          const win = new WebviewWindow(label, { url })
          immersivePending.current = true
          // 兜底跳转同样算"这一屏的收尾动作"：3 秒内切了对话，或用户改点「在此标准界面打开」，
          // 这次整页跳转已经不是用户要的了；created 事件也会把待定标记作废
          schedule(() => {
            if (immersivePending.current) window.location.href = url
          }, 3000)
          win.once('tauri://created', () => { immersivePending.current = false })
          win.once('tauri://error', () => { immersivePending.current = false; window.location.href = url })
        } catch {
          window.location.href = url  // 无权限/失败 → 回退应用内
        }
      })()
      return
    }
    // 网页端：命名窗口（同名复用+聚焦）；WebView/被弹窗拦截（返回 null）→ 回退应用内
    if (!tryOpenWorldWindow(boundWorldId, typeof conversationId === 'number' ? conversationId : undefined)) window.location.href = url
  }, [boundWorldId, conversationId, schedule])

  useEffect(() => {
    if (!conversationId) return
    setThinkingAgents(new Map())
    setTypingAgents(new Map())
    speakingTimers.current.forEach(clearTimeout)
    speakingTimers.current.clear()
    setSpeakingAgents(new Map())
    setMessages([])
    setHasMoreBefore(false)
    setHasMoreAfter(false)
    setFirstUnreadId(null)
    setShowJumpToUnread(false)
    setAtBottom(true)
    prevMessageCount.current = 0
    // 上一屏没走完的收尾动作不能带进新对话：pendingTimers 清了定时器，但"正在自动滚动"这个
    // 布尔标记得在这里归零——滚动监听看到它为真就整段 return，留着会让新对话的底部状态不再更新
    isAutoScrolling.current = false
    prevScrollHeight.current = 0
    immersivePending.current = false
    // 世界门属于「当前这个群」：上一屏的绑定状态与弹窗不带进新对话，
    // 否则切到未绑定世界的群，遮罩还盖着、按钮指向的还是上一个世界
    setBoundWorldId(null)
    setWorldModalOpen(false)
    setLoadingState('initial')
    // 进入对话时查询当前 AI 思考/输入中状态（组件重建后恢复活动指示器）
    const activityUrl = conversationType === 'group'
      ? `/groups/${conversationId}/activity`
      : `/dm/${conversationId}/activity`
    api.get<Record<string, {name: string; avatar_url: string | null; state?: string}>>(activityUrl).then(active => {
      const thinking = new Map<number, {name: string; avatarUrl: string | null}>()
      const typing = new Map<number, {name: string; avatarUrl: string | null}>()
      // 恢复的是三态，不是只有「思考中」：正在合成的语音要恢复成「录音中」，
      // 否则刷新一次，一个正在说话的人就变回在想
      Object.entries(active).forEach(([id, info]) => {
        const item = { name: info.name, avatarUrl: info.avatar_url }
        if (info.state === 'speaking') holdSpeaking(Number(id), item)
        else if (info.state === 'typing') typing.set(Number(id), item)
        else thinking.set(Number(id), item)
      })
      if (thinking.size > 0) setThinkingAgents(thinking)
      if (typing.size > 0) setTypingAgents(typing)
    }).catch(() => {})

    let cancelled = false
    const init = async () => {
      try {
        // 群视界门：群聊绑定世界 → 全屏弹窗（先不加载消息），选「标准界面」再加载
        if (conversationType === 'group') {
          try {
            const wr = await api.get<{ world_id: number }>(`/worlds/by-entity?entity_type=group&entity_id=${conversationId}`)
            if (wr.world_id) {
              if (cancelled) return
              setBoundWorldId(wr.world_id)
              setWorldModalOpen(true)
              return
            }
          } catch { /* 未绑定世界 */ }
          if (cancelled) return
        }
        await loadInitialMessages()
      } catch (err) {
        console.error('初始化失败:', err)
      }
    }
    init()
    return () => { cancelled = true }
  }, [conversationId, conversationType, loadInitialMessages, setAtBottom])

  // 初始加载后定位 + 立即保存已读位置（不等卸载，防止刷新时红线残留）
  useLayoutEffect(() => {
    if (loadingState === null && messages.length > 0 && prevMessageCount.current === 0) {
      prevMessageCount.current = messages.length
      // 立即保存已读位置：初始加载完成即视为用户已看到最新消息。
      // 取 messages 末条而不是 newestIdRef：镜像 effect 在这之后才跑，ref 此刻可能还是 null
      const newest = messages[messages.length - 1]?.id
      if (newest && conversationId) {
        const key = `lastRead_${conversationType}_${conversationId}`
        localStorage.setItem(key, String(newest))
      }
      const container = containerRef.current
      if (!container) return
      // 只把落在最近 AUTO_LOCATE_RECENT 条内的首条未读拉进视口；更早的未读不抢视口，
      // 停在最新（红线与「跳到首条未读」按钮仍在消息流里，往上翻是连续分页）
      const unreadIdx = firstUnreadId ? messages.findIndex(m => m.id === firstUnreadId) : -1
      const msgEl = unreadIdx >= 0 && messages.length - unreadIdx <= AUTO_LOCATE_RECENT
        ? container.querySelector(`[data-message-id="${firstUnreadId}"]`)
        : null
      if (msgEl) {
        // 首条未读就在附近 → 对齐到视口顶部（paint 之前，无闪烁）
        markAutoScrolling()
        // 只滚动消息容器本身，避免 scrollIntoView 连带滚动外层 main/Layout
        scrollToInContainer(container, msgEl as HTMLElement)
      } else if (container.scrollHeight > container.clientHeight) {
        // 没东西要定位（或未读在很远、虚拟列表还没渲染它）→ 停在最新
        container.scrollTop = container.scrollHeight
      }
    }
  }, [loadingState, messages, firstUnreadId, markAutoScrolling])

  // 上下哨兵：加载更早/更新的分页（游标在 ref 里，见 useSentinel 的注释）
  useSentinel({
    containerRef, sentinelRef: topSentinelRef, cursorRef: oldestIdRef,
    hasMore: hasMoreBefore, direction: 'older', loadingState, loadMessages,
  })
  useSentinel({
    containerRef, sentinelRef: bottomSentinelRef, cursorRef: newestIdRef,
    hasMore: hasMoreAfter, direction: 'newer', loadingState, loadMessages,
  })

  // 断线重连：把掉线期间漏掉的消息补上（WS 只推「此刻之后」的，掉线那一段没人补，
  // 不补的话列表就永久断成两截）。首次连接不算重连——那条路由初始加载负责。
  const wasConnectedRef = useRef(false)
  useEffect(() => {
    if (!connected) {
      wasConnectedRef.current = false
      return
    }
    if (wasConnectedRef.current) catchUpFrom(newestIdRef.current)
    wasConnectedRef.current = true
  }, [connected, catchUpFrom])

  // ============================================================
  // 滚动监听：isAtBottom + showJumpToUnread（使用 rAF 节流 DOM 查询）
  // ============================================================

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let rafId = 0
    const handleScroll = () => {
      if (isAutoScrolling.current) return
      if (rafId) return // 上一帧的 DOM 查询尚未执行，跳过
      rafId = requestAnimationFrame(() => {
        rafId = 0
        const { scrollTop: st, scrollHeight, clientHeight } = container
        // 虚拟列表：跟踪滚动位置与视口高度（值变化才触发渲染）
        setViewportH(clientHeight)
        setScrollTop(prev => (Math.abs(prev - st) > 1 ? st : prev))
        const atBottom = scrollHeight - st - clientHeight < BOTTOM_THRESHOLD
        setAtBottom(atBottom)

        if (firstUnreadId) {
          const msgEl = container.querySelector(`[data-message-id="${firstUnreadId}"]`)
          const visible = msgEl
            ? msgEl.getBoundingClientRect().bottom >= container.getBoundingClientRect().top
            : false
          // 仅值变化时才触发渲染
          if (visible !== showJumpToUnreadRef.current) {
            showJumpToUnreadRef.current = !visible
            setShowJumpToUnread(!visible)
          }
        }
      })
    }
    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => {
      container.removeEventListener('scroll', handleScroll)
      if (rafId) cancelAnimationFrame(rafId)
    }
  }, [firstUnreadId])

  // ============================================================
  // 未读：贴底 = 正在读
  // ============================================================

  // 上报「我贴在这个会话底部」：侧边栏据此不画红数泡——是判定，不是等后端回传再抹掉
  useEffect(() => {
    if (!conversationId || !isAtBottom) {
      setFollowingConversation(null)
      return
    }
    setFollowingConversation(conversationKey(conversationType, conversationId))
    return () => setFollowingConversation(null)
  }, [conversationType, conversationId, isAtBottom])

  const reading = useReadingKey() === conversationKey(conversationType, conversationId)

  const syncRead = useCallback(() => {
    if (!conversationId) return
    const url = conversationType === 'group'
      ? `/groups/${conversationId}/read`
      : `/dm/${conversationId}/read`
    api.post(url)
      // 落库成功才清本地那一项：反过来的话，读失败时侧栏会假装读过了
      .then(() => markConversationRead(conversationType, conversationId))
      .catch(() => {})
  }, [conversationType, conversationId])

  // 正在读，就把这一屏的已读落回后端。触发它的四件事——进会话（贴底初始为真）、
  // 滚回底部、收到新消息、标签页切回前台——都落在 reading 或「最新一条」的变化上
  const newestId = messages.length > 0 ? messages[messages.length - 1].id : null
  useEffect(() => {
    if (!reading) return
    syncRead()
  }, [reading, newestId, syncRead])

  // ============================================================
  // Mermaid 错误报告
  // ============================================================
  // 用 ref 存 handleSend，避免每次渲染重建事件监听
  const handleSendRef = useRef<typeof handleSend>(null as any)
  // handleSendRef.current 在后面 handleSend 定义后赋值
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (detail?.message) {
        handleSendRef.current(detail.message)
      }
    }
    document.addEventListener('mermaid-error-report', handler)
    return () => document.removeEventListener('mermaid-error-report', handler)
  }, [])

  // ============================================================
  // @提及逻辑（仅群聊）
  // ============================================================

  // 文件上传处理（超限/失败记成错误态附件，不再 alert 打断流程）
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) attachments.pick(e.target.files)
    // 清空 input 以便重复选择同一文件
    e.target.value = ''
  }


  const handleSend = (text: string) => {
    if (!text.trim() && attachments.items.length === 0) return
    if (!conversationId) return
    if (!connected) return

    // 还有上传中 / 上传失败的文件 → 不发（hook 已把状态标在附件条上）
    if (attachments.uploading || attachments.hasError) return

    const readyAttachments = attachments.ready
    sendMessage(text.trim(), replyTo?.id, readyAttachments.length > 0 ? readyAttachments : undefined)
    setInput('')
    setReplyTo(null)
    // 发送后清除输入中状态
    if (typingRef.current) {
      typingRef.current = false
      sendTyping(false)
    }
    localStorage.removeItem(`draft_${conversationType}_${conversationId}`)
    attachments.clear()
    window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT, { detail: { type: 'message_sent' } }))
  }
  // 故意在 render 里赋值（不放进 effect）：mermaid 错误事件可能在本轮 effect 跑完前就到，
  // 走 effect 同步会在那之前拿到 null
  handleSendRef.current = handleSend

  // ============================================================
  // 渲染
  // ============================================================

  // 世界门是「群聊面板」级浮层：本组件里的 absolute 只到消息区，面板顶部标题栏会露在遮罩外面，
  // 所以整块交给 ChatArea 的面板容器渲染；宿主还没就绪时就地渲染，避免门没出现而消息也不加载
  // 头部下面是常驻的「在沉浸式界面打开」入口（只在绑定世界的群里）。它不占布局，
  // 但消息顶部要给它让出高度：WORLD_STRIP_H 就是这条的高度
  const showWorldStrip = conversationType === 'group' && boundWorldId != null && !worldModalOpen
  // 顶部提示条：统一交给 NoticeStack 排（order 小的在上）。重连比世界入口更急，所以压在它上面；
  // 它的总高度由下面 --header-strip 让给消息区，条浮着、消息不会钻到条底下
  const notices: NoticeItem[] = []
  if (reconnecting) {
    notices.push({
      id: 'reconnecting', order: 10, tone: 'accent',
      icon: <Loader2 size={12} className="animate-spin" />,
      text: t('chat:reconnecting'),
    })
  }
  if (showWorldStrip) {
    notices.push({
      id: 'world-immersive', order: 20, tone: 'primary',
      icon: <Gamepad2 size={12} />,
      text: t('chat:worldEnterImmersive'),
      onClick: openImmersive,
    })
  }
  const worldGate = worldModalOpen && boundWorldId != null && conversationType === 'group' ? (
    <div className="absolute inset-0 z-toast flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md mx-4 max-h-full overflow-y-auto bg-surface rounded-dialog border border-primary-500/30 shadow-2xl p-8 text-center">
        <div className="mb-4"><Globe size={48} className="mx-auto text-primary-400" /></div>
        <h2 className="text-lg font-semibold text-textPrimary">{t('chat:worldGateTitle')}</h2>
        <p className="text-sm text-textMuted mt-2 mb-7">{t('chat:worldGateSubtitle')}</p>
        <div className="space-y-2.5">
          <button onClick={openImmersive} className="btn btn-md btn-primary w-full gap-1.5">
            <Gamepad2 size={14} /> {t('chat:worldGateImmersive')}
          </button>
          <button
            onClick={closeWorldModal}
            className="btn btn-md btn-outline w-full"
          >
            <Settings size={12} /> {t('chat:worldGateStandard')}
          </button>
        </div>
      </div>
    </div>
  ) : null

  return (
    // min-h-0 不能省：没有它 flex 项的 min-height 是 auto，消息区会按内容撑高、
    // 底部输入区被顶到它下面去，看着就是"输入框压住了最后几条消息"（玻璃改成透明后才看得见）
    <div className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden relative">
      {/* 顶部提示条：统一一处渲染、按 order 从上往下排，高度由 --header-strip 让给消息区 */}
      <NoticeStack items={notices} />

      {/* 隐藏的文件输入（附件按钮触发） */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileSelect}
      />

      {/* 错误 Toast */}
      {errors.length > 0 && (
        <div className="absolute right-4 z-modal space-y-1 max-w-sm" style={{ top: 'calc(var(--header-inset, 0px) + 1rem)' }}>
          {errors.map((err) => (
            <div
              key={err.timestamp}
              className="flex items-start gap-2 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-card px-3 py-2 text-sm shadow-lg shadow-black/20"
            >
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span className="flex-1">{err.message}</span>
              <button onClick={clearErrors} className="shrink-0 text-rose-400/60 hover:text-rose-400">
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 撤回提示：通道侧没撤掉这类"半成功"必须让人看见，不能假装全撤了 */}
      {revokeNotice && (
        <div className="absolute right-4 z-modal max-w-sm" style={{ top: 'calc(var(--header-inset, 0px) + 0.75rem)' }}>
          <div className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/20 text-amber-500 rounded-card px-3 py-2 text-sm shadow-lg shadow-black/20">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            <span className="flex-1">{revokeNotice}</span>
            <button onClick={() => setRevokeNotice(null)} className="shrink-0 text-amber-500/60 hover:text-amber-500">
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      {/* ↑ 跳至首个未读消息：顶部有浮起的头部，这颗必须落在它下沿，不能用 top-3 */}
      {showJumpToUnread && firstUnreadId && (
        <button
          onClick={handleJumpToUnread}
          className="btn btn-sm btn-primary absolute top-3 right-4 z-drawer shadow-lg shadow-primary-500/30 backdrop-blur-sm"
          style={{ top: 'calc(var(--header-inset, 0px) + 0.5rem)' }}
        >
          <ArrowUp size={14} />
          {t('chat:jumpToFirstUnread')}
        </button>
      )}

      {/* 群视界全屏入口：渲染进群聊面板（见 worldGate），先弹窗、不加载消息；选「标准界面」才关并加载 */}
      {worldGate && (overlayHost ? createPortal(worldGate, overlayHost) : worldGate)}

      {/* 消息列表：外层不滚动，专门用来挂拖拽蒙版（放进滚动容器会随内容滚走）；
          containerRef 必须留在内层——虚拟列表靠它读 scrollTop */}
      <div
        className="flex-1 min-h-0 relative"
        // 输入区浮在消息上，它占掉的高度只有 composerH 一个来源，但要用两种载体送出去：
        // ① --composer-inset（CSS 变量，给 .chat-scroll 的 padding-bottom 用——只有 CSS 能读变量）
        // ② data-composer-inset（下一层滚动容器上的属性，给 MobileScrollbar 量轨道用——
        //    它在滚动回调里读数，读 dataset 是纯 JS，读变量得 getComputedStyle，每帧强制一次样式重算）
        // 两处都是同一个数字，改高度时不用管这里，ResizeObserver 会把两者一起刷新
        style={{
          '--composer-inset': `${composerH}px`,
          // 头部下面压着的提示条（重连、沉浸入口…）：条数 × 每条高度，一起算进顶部留白
          '--header-strip': notices.length ? `calc(${notices.length} * ${NOTICE_H})` : '0px',
        } as React.CSSProperties}
        {...attachments.zoneProps('list')}
      >
        <DropMask {...attachments.dropState('list')} label={t('chat:dropToAdd')} />
        <div
          ref={containerRef}
          className="absolute inset-0 overflow-y-auto px-2 md:px-4 bg-canvas chat-scroll"
          // 上一条注释里的 ②：滚动条读这个属性算轨道底边
          data-composer-inset={composerH}
        >
          {/* 顶部哨兵（加载更旧消息的触发器） */}
          <div ref={topSentinelRef} className="h-1" />

          {/* 顶部加载指示器 */}
          {loadingState === 'older' && (
            <div className="flex items-center justify-center py-3">
              <Loader2 className="animate-spin text-textMuted" size={16} />
            </div>
          )}

          {/* 无更多旧消息提示 */}
          {!hasMoreBefore && messages.length > 0 && (
            <div className="text-center text-3xs text-textMuted py-2 select-none">
              {t('chat:beginningOfChat')}
            </div>
          )}

          {/* 初始加载 */}
          {loadingState === 'initial' ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="animate-spin text-textMuted" size={24} />
            </div>
          ) : messages.length === 0 ? (
            <EmptyState icon={MessageSquare} title={conversationType === 'dm' ? '开始私信' : '开始群聊'} description={conversationType === 'dm' ? '给对方发送第一条消息吧' : '在群里发送第一条消息吧'} />
          ) : (
            <div style={{ paddingTop: messageElements.beforeH, paddingBottom: messageElements.afterH }}>
              {messageElements.items}
            </div>
          )}

          {/* 底部加载指示器 */}
          {loadingState === 'newer' && (
            <div className="flex items-center justify-center py-3">
              <Loader2 className="animate-spin text-textMuted" size={16} />
            </div>
          )}

          {/* 底部哨兵（加载更新消息的触发器） */}
          <div ref={bottomSentinelRef} className="h-1" />
        </div>

        {/* ↓ 回到底部浮动按钮 —— 必须挂在消息区里：挂到面板根上，bottom-* 量的是
            整块面板（含输入区），按钮就会落进输入框里 */}
        {!isAtBottom && messages.length > 0 && (
          <div className="absolute right-4 z-drawer" style={{ bottom: composerH + 16 }}>
            <button onClick={() => scrollToBottom(true)} className="float-btn" title={t('chat:scrollToBottom')}>
              <ArrowDown size={16} />
            </button>
          </div>
        )}
      </div>

      {/* 输入区浮在消息之上：不占布局空间，消息从它底下滚过去（和手机端底部菜单同一个做法）。
          高度由 ResizeObserver 量出来，垫进消息列表的 paddingBottom */}
      <div
        ref={composerRef}
        className="absolute inset-x-0 bottom-0 z-10 px-3 pt-1 pb-3"
        // 与手机端悬浮菜单同一条安全区规则：刘海屏底部留白算进去，胶囊才不会被手势条压住
        style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))' }}
        {...attachments.zoneProps('input')}
        {...attachments.pasteProps}
      >
        <DropMask {...attachments.dropState('input')} label={t('chat:dropToAdd')} />
        {/* 高度拖拽手柄：跟着输入区一起浮起来（原来在流里，飘到消息上方就够不着了）。
            只在桌面出现——手机上拖它和滚动消息区抢手势；平时是一条 8px 的透明带，
            悬停才显出一道细把手（浮起来之后没了 hover 反馈，看着就不像能拖） */}
        <div
          className="group/handle hidden md:flex absolute inset-x-0 -top-1 h-2 items-center justify-center cursor-row-resize"
          title={t('chat:dragToResize')}
          onMouseDown={(e) => {
            inputResizeRef.current = true
            const startY = e.clientY
            // 从当前视觉高度起拖：inputHeight 存的就是总高的下限，自动增长那部分已经在里面
            const startTotalH = Math.max(CHAT_INPUT_MIN_H, textareaRef.current?.offsetHeight || 0)
            const onMove = (ev: MouseEvent) => {
              if (!inputResizeRef.current) return
              const deltaY = startY - ev.clientY  // 向上拖=正值=扩大
              setInputHeight(Math.max(CHAT_INPUT_MIN_H, startTotalH + deltaY))
            }
            const onUp = () => {
              inputResizeRef.current = false
              window.removeEventListener('mousemove', onMove)
              window.removeEventListener('mouseup', onUp)
            }
            window.addEventListener('mousemove', onMove)
            window.addEventListener('mouseup', onUp)
          }}
        >
          <span className="h-[3px] w-10 rounded-full bg-textMuted/0 transition-colors group-hover/handle:bg-textMuted/45" />
        </div>
        {/* 活动状态栏：贴输入框上沿，跟输入区一起浮起来（它的高度会被 composerH 量进去，
            消息区留白自动让开，不会压住最后一条） */}
        <ActivityBar users={activityUsers} />

        {/* 附件预览列表（与群视界世界对话共用同一组件） */}
        <AttachmentChips items={attachments.items} onRemove={attachments.remove} errorText={t('chat:uploadFailed')} />

        {replyTo && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-t-xl border border-border bg-surface/50">
            {/* 与气泡里的引用同一份实现：竖线、名字在前、缩略图跟名字后面 */}
            <QuotedMessage
              sender={replyTo.sender_name}
              content={replyTo.content}
              image={firstImageAttachment(messageById.get(replyTo.id)?.attachments)}
              prefix="回复 "
              lines={1}
              thumbClass="w-7 h-7"
            />
            <button onClick={() => setReplyTo(null)} className="icon-btn icon-btn-sm shrink-0">
              <X size={14} />
            </button>
          </div>
        )}
        <ChatInput
          ref={textareaRef}
          conversationType={conversationType}
          conversationId={conversationId}
          t={t}
          onSend={handleSend}
          connected={connected}
          onSendFile={() => fileInputRef.current?.click()}
          hasAttachments={attachments.items.length > 0}
          groupMembers={groupMembers}
          inputHeight={inputHeight}
        />
      </div>

      {/* 资料卡 */}
      {/* 手机端滚动条：轨道止于浮起的输入区上沿（不传 bottomInset 会一路画到输入框底下） */}
      {/* 全端同一根：手机上它为了绕开悬浮输入区，桌面上同样没有原生条（.chat-scroll 已收起） */}
      <MobileScrollbar containerRef={containerRef} always />

      {profileCard && (
        <ProfileCard
          entityType={profileCard.type as 'human' | 'ai'}
          entityId={profileCard.id}
          entityName={profileCard.name}
          state={profileCard.state}
          onClose={() => setProfileCard(null)}
        />
      )}
    </div>
  )
}
