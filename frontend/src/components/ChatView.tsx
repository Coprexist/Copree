import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react'
import { useWebSocket, type WebSocketMessage } from '../hooks/useWebSocket'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import MessageBubble from './MessageBubble'
import ChatInput from './ChatInput'
// 阈值（距底多少算「在底部」）与群视界/DSH 对话同一份来源；本组件有自己的虚拟列表机器，不套整个 hook
import { BOTTOM_THRESHOLD } from '../hooks/useStickToBottom'
import ActivityBar, { type ActivityUser } from './ActivityBar'
import ProfileCard from './ProfileCard'
import { EmptyState, MenuPanel, MenuItem } from './ui'
import { Send, Loader2, AlertTriangle, X, ArrowDown, ArrowUp, Paperclip, FileIcon, Bot, User, MessageSquare, Inbox, Settings , Gamepad2 , Globe } from 'lucide-react'
import { getStateDotColor, CHAT_REFRESH_EVENT } from '../constants'
import { useT } from '../i18n/I18nContext'
import { tryOpenWorldWindow } from '../utils/worldView'
import { isTauri, onKeyboardChange } from '../utils/tauri'
import { scrollToInContainer } from '../utils/scroll'
import { useAttachmentUpload } from '../hooks/useAttachmentUpload'
import { AttachmentChips, DropMask } from './AttachmentChips'

// ── 虚拟列表：消息高度估算（纯函数，窗口化渲染用）──
// 估算偏保守（偏大），配合 overscan 消化误差，避免滚动时窗口露出空白
const estimateMessageHeight = (msg: any): number => {
  const base = 44                        // 气泡 + 间距基础
  const text = msg.content || ''
  const lines = Math.max(1, Math.ceil(text.length / 28))  // 每行约 28 字符
  const textH = lines * 20
  const attachH = (msg.attachments?.length || 0) * 88     // 附件/图片
  const replyH = msg.reply_to ? 26 : 0                     // 回复引用
  return base + textH + attachH + replyH
}

const VIRTUAL_OVERSCAN = 40   // 窗口外预渲染条数（估算误差缓冲）
const OVERSCAN_PX = 1200      // 视口上下各多渲染的像素

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
  created_at: string
}

interface ChatViewProps {
  conversationType: 'group' | 'dm'
  conversationId: number | string
}

const PAGE_SIZE = 20

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

export default function ChatView({ conversationType, conversationId }: ChatViewProps) {
  // ── 群视界：群聊绑定的世界（全屏入口弹窗，先不加载消息） ──
  const [boundWorldId, setBoundWorldId] = useState<number | null>(null)
  const [worldModalOpen, setWorldModalOpen] = useState(false)
  const t = useT()
  const { user } = useAuth()
  const [messages, setMessages] = useState<Message[]>([])
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
  // 人类打字状态: userId → {name, avatarUrl}
  const [humanTyping, setHumanTyping] = useState<Map<number, { name: string; avatarUrl: string | null }>>(new Map())

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
    humanTyping.forEach(({ name, avatarUrl }, id) => {
      map.set(id, { id, name, avatarUrl, status: 'typing' })
    })

    return Array.from(map.values())
  }, [thinkingAgents, typingAgents, humanTyping])

  // @提及 自动补全（仅群聊）
  const [groupMembers, setGroupMembers] = useState<Array<{ type: string; id: number; name: string; state?: string }>>([])
  // 私信对方的类型（判断这场对话有没有 AI；群聊直接看成员表）
  const [peerType, setPeerType] = useState<string | null>(null)
  const [mentionActive, setMentionActive] = useState(false)
  const [replyTo, setReplyTo] = useState<{ id: number; sender_name: string; content: string } | null>(null)
  const [mentionQuery, setMentionQuery] = useState('')
  const [mentionIdx, setMentionIdx] = useState(0)

  // 文件附件：上传逻辑与群视界世界对话共用 useAttachmentUpload
  //（图片如何进 LLM 多模态由后端 app/utils/multimodal.py 统一处理）
  const attachments = useAttachmentUpload()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Refs
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const inputRef = useRef(input)
  inputRef.current = input // 保持同步，供 effect cleanup 闭包读取最新值
  const containerRef = useRef<HTMLDivElement>(null)

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
  const newestIdRef = useRef<number | null>(null)       // 离开时保存已读位置
  const oldestIdRef = useRef<number | null>(null)        // 供哨兵读取，避免 messages 依赖
  const typingRef = useRef(false)                       // 当前是否正在输入中，避免重复发送 typing 消息
  const showJumpToUnreadRef = useRef(false)              // 避免 scroll 中高频 setState

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

  const handleMessage = useCallback((msg: WebSocketMessage) => {
    if (msg.type === 'message') {
      const m = msg.data
      // 过滤：仅处理当前对话的消息（防止 DM 邀请卡闪现到群聊）
      if (!isMessageForThisConversation(m, conversationType, conversationId)) return
      setMessages((prev) => {
        // 去重：防止 WebSocket 与 HTTP fetch 竞态导致同 ID 消息重复
        if (prev.some((existing) => existing.id === m.id)) return prev
        const next = [...prev, m]
        newestIdRef.current = m.id
        return next
      })
      if (isAtBottomRef.current) {
        setTimeout(() => scrollToBottom(true), 50)
      }
      // 新消息到达 → 通知侧栏刷新排序和未读
      window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT, { detail: {
        type: 'unread_update',
        conversation_type: conversationType,
        conversation_id: conversationId,
      } }))
      if (m.sender_type === 'ai' && m.sender_id) {
        removeFromMap(setThinkingAgents, m.sender_id)
        removeFromMap(setTypingAgents, m.sender_id)
      }
    } else if (msg.type === 'ai_thinking') {
      const d = msg.data
      if (d.trigger === 'auto') return
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      addToMap(setThinkingAgents, d.user_id, { name: d.agent_name, avatarUrl: d.agent_avatar_url || null })
    } else if (msg.type === 'ai_thinking_end') {
      const d = msg.data
      if (d.trigger === 'auto') return
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      removeFromMap(setThinkingAgents, d.user_id)
      removeFromMap(setTypingAgents, d.user_id)
    } else if (msg.type === 'ai_typing') {
      const d = msg.data
      if (d.trigger === 'auto') return
      if (!isMessageForThisConversation(d, conversationType, conversationId)) return
      setTimeout(() => removeFromMap(setThinkingAgents, d.user_id), 1500)
      addToMap(setTypingAgents, d.user_id, { name: d.agent_name, avatarUrl: d.agent_avatar_url || null })
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
        sender_name: t('groupSettings.announcement'),
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
  }, [conversationType, conversationId, t])

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
  const heights = useMemo(() => messages.map(m => estimateMessageHeight(m)), [messages])
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

  const messageElements = useMemo(() => {
    const { s, e } = windowRange
    const items = messages.slice(s, e).map((msg) => {
      const replyToData = msg.reply_to == null ? undefined : (() => {
        const quoted = messages.find(m => m.id === msg.reply_to)
        if (!quoted) return { id: msg.reply_to, sender: '?', content: '' }
        return { id: quoted.id, sender: quoted.sender_name || `用户${quoted.sender_id}`, content: quoted.content.slice(0, 80) }
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
              <span className="text-3xs font-medium text-rose-400 whitespace-nowrap">{t('chat.newMessages')}</span>
              <div className="flex-1 h-px bg-rose-500/30" />
            </div>
          )}
          <MessageBubble
            senderName={msg.sender_name || `${msg.sender_type}:${msg.sender_id}`}
            senderAvatarUrl={msg.sender_avatar_url}
            content={msg.content}
            isMine={isOwnMessage(msg)}
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
          />
        </div>
      )
    })
    return {
      beforeH: cumHeights[s],
      afterH: totalHeight - cumHeights[e],
      items,
    }
  }, [windowRange, messages, cumHeights, totalHeight, firstUnreadId, hasMoreBefore, isOwnMessage, handleAvatarClick, t])

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

      // 更新 refs（避免 IntersectionObserver 依赖 messages 数组）
      if (fetched.length > 0) {
        newestIdRef.current = fetched[fetched.length - 1].id
        oldestIdRef.current = fetched[0].id
      }

      setMessages((prev) => {
        if (mode === 'older') {
          // 去重：fetched 可能在 prev 中已存在（竞态）
          const existingIds = new Set(prev.map((m) => m.id))
          const unique = fetched.filter((m) => !existingIds.has(m.id))
          return [...unique, ...prev]
        } else if (mode === 'newer') {
          // 游标 after_id 保证不重叠，但以防万一也去重
          const existingIds = new Set(prev.map((m) => m.id))
          const unique = fetched.filter((m) => !existingIds.has(m.id))
          return [...prev, ...unique]
        } else {
          return fetched
        }
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
          } else if (gotFullPage) {
            // 首次访问且有更多历史消息 → 最旧那条作为未读边界
            setFirstUnreadId(fetched[0].id)
          }
          // 首次访问且消息 ≤ PAGE_SIZE → 全部可见，无需未读标记
        }
      }
    } catch (err) {
      console.error('加载消息失败:', err)
    } finally {
      setLoadingState(null)
    }
  }, [conversationId, conversationType])

  // ============================================================
  // 滚动辅助
  // ============================================================

  const scrollToBottom = useCallback((smooth = true) => {
    const el = containerRef.current
    if (!el) return
    isAutoScrolling.current = true
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'instant' })
    setTimeout(() => { isAutoScrolling.current = false }, 500)
  }, [])

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
        isAutoScrolling.current = true
        // 只滚动消息容器本身（scrollIntoView 会连带滚动外层 main/Layout，把标题栏滚出视口）
        scrollToInContainer(el, msgEl as HTMLElement, { smooth: true })
        setTimeout(() => { isAutoScrolling.current = false }, 500)
      }
    })
  }, [messages, cumHeights])

  const handleJumpToUnread = useCallback(async () => {
    if (!firstUnreadId) return
    // 加载 firstUnreadId 之前的一页消息
    await loadMessages({ before_id: firstUnreadId, mode: 'older' })
    // 滚动到原来的 firstUnreadId 位置
    setTimeout(() => scrollToMessage(firstUnreadId), 200)
  }, [firstUnreadId, loadMessages, scrollToMessage])

  // ============================================================
  // 初始加载
  // ============================================================

  const loadInitialMessages = useCallback(async () => {
    if (!conversationId) return
    if (conversationType === 'group') {
      api.post(`/groups/${conversationId}/read`)
        .then(() => {
          window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT, { detail: { type: 'unread_update' } }))
        })
        .catch(() => {})
      const membersData = await api.get(`/groups/${conversationId}/members`)
      setGroupMembers(membersData)
    } else {
      // 只取会话元信息（summary=true，不拉消息）：只为知道对方是不是 AI
      api.get<{ partner?: { type?: string } }>(`/dm/${conversationId}?summary=true`)
        .then((d) => setPeerType(d?.partner?.type ?? null))
        .catch(() => setPeerType(null))
    }
    await loadMessages({ mode: 'initial' })
    // DM 消息加载同时标记已读，触发 sidebar 刷新未读计数
    if (conversationType === 'dm') {
      window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT, { detail: { type: 'unread_update' } }))
    }
  }, [conversationId, conversationType, loadMessages])

  // 全屏弹窗：选择「在此标准界面打开」→ 关弹窗 + 加载消息
  const closeWorldModal = useCallback(() => {
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
          const failTimer = setTimeout(() => { window.location.href = url }, 3000)
          win.once('tauri://created', () => clearTimeout(failTimer))
          win.once('tauri://error', () => { clearTimeout(failTimer); window.location.href = url })
        } catch {
          window.location.href = url  // 无权限/失败 → 回退应用内
        }
      })()
      return
    }
    // 网页端：命名窗口（同名复用+聚焦）；WebView/被弹窗拦截（返回 null）→ 回退应用内
    if (!tryOpenWorldWindow(boundWorldId, typeof conversationId === 'number' ? conversationId : undefined)) window.location.href = url
  }, [boundWorldId, conversationId])

  useEffect(() => {
    if (!conversationId) return
    setThinkingAgents(new Map())
    setTypingAgents(new Map())
    setMessages([])
    setHasMoreBefore(false)
    setHasMoreAfter(false)
    setFirstUnreadId(null)
    setShowJumpToUnread(false)
    setIsAtBottom(true)
    prevMessageCount.current = 0
    setLoadingState('initial')
    // 进入对话时查询当前 AI 思考/输入中状态（组件重建后恢复活动指示器）
    const activityUrl = conversationType === 'group'
      ? `/groups/${conversationId}/activity`
      : `/dm/${conversationId}/activity`
    api.get<Record<string, {name: string; avatar_url: string | null}>>(activityUrl).then(active => {
      const thinking = new Map<number, {name: string; avatarUrl: string | null}>()
      Object.entries(active).forEach(([id, info]) => {
        thinking.set(Number(id), { name: info.name, avatarUrl: info.avatar_url })
      })
      if (thinking.size > 0) setThinkingAgents(thinking)
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
  }, [conversationId, conversationType, loadInitialMessages])

  // 初始加载后定位 + 立即保存已读位置（不等卸载，防止刷新时红线残留）
  useLayoutEffect(() => {
    if (loadingState === null && messages.length > 0 && prevMessageCount.current === 0) {
      prevMessageCount.current = messages.length
      // 立即保存已读位置：初始加载完成即视为用户已看到最新消息
      if (newestIdRef.current && conversationId) {
        const key = `lastRead_${conversationType}_${conversationId}`
        localStorage.setItem(key, String(newestIdRef.current))
      }
      const container = containerRef.current
      if (!container) return
      if (firstUnreadId) {
        // 有更早的未读消息 → 将首个未读定位到视口顶部（paint 之前，无闪烁）
        const msgEl = container.querySelector(`[data-message-id="${firstUnreadId}"]`)
        if (msgEl) {
          isAutoScrolling.current = true
          // 只滚动消息容器本身，避免 scrollIntoView 连带滚动外层 main/Layout
          scrollToInContainer(container, msgEl as HTMLElement)
          setTimeout(() => { isAutoScrolling.current = false }, 500)
        }
      } else if (container.scrollHeight > container.clientHeight) {
        // 无旧消息但内容溢出 → 滚到底部
        container.scrollTop = container.scrollHeight
      }
    }
  }, [loadingState, messages.length, firstUnreadId])

  // ============================================================
  // 共享哨兵 Hook：IntersectionObserver 用 ref 读取消息 ID，避免 messages 数组依赖
  // ============================================================

  function useSentinel(
    sentinelRef: React.RefObject<HTMLDivElement | null>,
    hasMore: boolean,
    direction: 'older' | 'newer',
  ) {
    useEffect(() => {
      const sentinel = sentinelRef.current
      if (!sentinel) return
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting && hasMore && !loadingState) {
            const cursorId = direction === 'older' ? oldestIdRef.current : newestIdRef.current
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
    }, [hasMore, loadingState, direction])
  }

  useSentinel(topSentinelRef, hasMoreBefore, 'older')
  useSentinel(bottomSentinelRef, hasMoreAfter, 'newer')

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
        setIsAtBottom(atBottom)
        isAtBottomRef.current = atBottom

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

  const mentionFiltered = useMemo(() => mentionQuery
    ? groupMembers.filter((m) => m.name.toLowerCase().includes(mentionQuery.toLowerCase()))
    : groupMembers
  , [mentionQuery, groupMembers])

  const detectMention = (value: string, cursorPos: number) => {
    if (conversationType === 'dm') return
    const beforeCursor = value.slice(0, cursorPos)
    const atIdx = beforeCursor.lastIndexOf('@')
    if (atIdx === -1) { setMentionActive(false); return }
    const prev = atIdx > 0 ? beforeCursor[atIdx - 1] : ''
    const allowed = prev === '' || prev === ' ' || /[，。！？、；：""【】（）]/.test(prev)
    if (!allowed) { setMentionActive(false); return }
    const query = beforeCursor.slice(atIdx + 1, cursorPos)
    if (query.includes(' ')) { setMentionActive(false); return }
    setMentionQuery(query)
    setMentionIdx(0)
    setMentionActive(true)
  }

  const insertMention = (name: string) => {
    const ta = textareaRef.current
    if (!ta) return
    const value = input
    const cursorPos = ta.selectionStart
    const beforeCursor = value.slice(0, cursorPos)
    const atIdx = beforeCursor.lastIndexOf('@')
    if (atIdx === -1) return
    const newBefore = beforeCursor.slice(0, atIdx) + '@' + name + ' '
    const newValue = newBefore + value.slice(cursorPos)
    setInput(newValue)
    setMentionActive(false)
    requestAnimationFrame(() => {
      ta.focus()
      const newPos = newBefore.length
      ta.setSelectionRange(newPos, newPos)
    })
  }

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
    setMentionActive(false)
    window.dispatchEvent(new CustomEvent(CHAT_REFRESH_EVENT, { detail: { type: 'message_sent' } }))
  }
  handleSendRef.current = handleSend

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (mentionActive && mentionFiltered.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIdx((prev) => (prev + 1) % mentionFiltered.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMentionIdx((prev) => (prev - 1 + mentionFiltered.length) % mentionFiltered.length); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insertMention(mentionFiltered[mentionIdx].name); return }
      if (e.key === 'Escape') { e.preventDefault(); setMentionActive(false); return }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); }
  }

  function isOwnMessage(msg: Message) {
    return msg.sender_type === 'human' && msg.sender_id === user?.id
  }

  // ============================================================
  // 渲染
  // ============================================================

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden relative">
      {/* 重连提示条 */}
      {reconnecting && (
        <div className="absolute top-0 left-0 right-0 z-modal flex items-center justify-center gap-2 bg-accent-500/15 border-b border-accent-500/20 text-accent-400 px-4 py-1.5 text-xs font-medium backdrop-blur-sm">
          <Loader2 size={12} className="animate-spin" />
          {t('chat.reconnecting')}
        </div>
      )}

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
        <div className="absolute top-4 right-4 z-modal space-y-1 max-w-sm">
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

      {/* ↑ 跳至首个未读消息 */}
      {showJumpToUnread && firstUnreadId && (
        <button
          onClick={handleJumpToUnread}
          className="absolute top-3 right-4 z-drawer flex items-center gap-1.5 px-3 py-1.5 bg-primary-500/90 hover:bg-primary-500 text-white text-xs font-medium rounded-full shadow-lg shadow-primary-500/30 backdrop-blur-sm transition-all duration-200"
        >
          <ArrowUp size={14} />
          {t('chat.jumpToFirstUnread')}
        </button>
      )}

      {/* 群视界全屏入口：群聊绑定世界时先弹窗、不加载消息；选「标准界面」才关并加载 */}
      {worldModalOpen && boundWorldId && conversationType === 'group' && (
        <div className="absolute inset-0 z-toast flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md mx-4 bg-surface rounded-dialog border border-primary-500/30 shadow-2xl p-8 text-center">
            <div className="mb-4"><Globe size={48} className="mx-auto text-primary-400" /></div>
            <h2 className="text-lg font-semibold text-textPrimary">这个群聊绑定了群视界</h2>
            <p className="text-sm text-textMuted mt-2 mb-7">世界已就绪，选择一种方式进入</p>
            <div className="space-y-2.5">
              <button
                onClick={openImmersive}
                className="btn btn-md btn-primary w-full gap-1.5"
              >
                <Gamepad2 size={14} /> 在沉浸界面打开
              </button>
              <button
                onClick={closeWorldModal}
                className="w-full inline-flex items-center justify-center gap-1.5 py-3 bg-elevated hover:bg-border text-textPrimary rounded-card font-medium transition-colors"
              >
                <Settings size={12} /> 在此标准界面打开
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 消息列表：外层不滚动，专门用来挂拖拽蒙版（放进滚动容器会随内容滚走）；
          containerRef 必须留在内层——虚拟列表靠它读 scrollTop */}
      <div className="flex-1 min-h-0 relative" {...attachments.zoneProps('list')}>
        <DropMask {...attachments.dropState('list')} label={t('chat.dropToAdd')} />
        <div
          ref={containerRef}
          className="absolute inset-0 overflow-y-auto px-4 py-4 bg-canvas"
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
              {t('chat.beginningOfChat')}
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

          {/* 底部活动状态栏：合并 AI 思考/输入 + 人类打字 */}
          <ActivityBar users={activityUsers} />

          {/* 底部加载指示器 */}
          {loadingState === 'newer' && (
            <div className="flex items-center justify-center py-3">
              <Loader2 className="animate-spin text-textMuted" size={16} />
            </div>
          )}

          {/* 底部哨兵（加载更新消息的触发器） */}
          <div ref={bottomSentinelRef} className="h-1" />
        </div>
      </div>

      {/* ↓ 回到底部浮动按钮 */}
      {!isAtBottom && messages.length > 0 && (
        <div className="absolute bottom-20 right-6 z-drawer">
          <button
            onClick={() => scrollToBottom(true)}
            className="flex items-center justify-center w-9 h-9 bg-elevated border border-border rounded-full shadow-lg shadow-black/20 text-textSecondary hover:text-textPrimary hover:bg-surface transition-all duration-200"
            title={t('chat.scrollToBottom')}
          >
            <ArrowDown size={16} />
          </button>
        </div>
      )}

      {/* 输入框拖拽调整手柄 */}
      <div
        className="relative h-1.5 cursor-row-resize hover:bg-primary-400/30 active:bg-primary-400/50 transition-colors shrink-0"
        onMouseDown={(e) => {
          inputResizeRef.current = true
          const startY = e.clientY
          const ta = textareaRef.current
          const autoH = parseInt(ta?.dataset.autoHeight || '0', 10)
          const startInputH = inputHeight || 0
          const startTotalH = Math.max(44, startInputH + autoH)
          const onMove = (ev: MouseEvent) => {
            if (!inputResizeRef.current) return
            const deltaY = startY - ev.clientY  // 向上拖=正值=扩大
            const newTotalH = Math.max(44, startTotalH + deltaY)
            const newInputH = newTotalH - autoH
            setInputHeight(newInputH)
          }
          const onUp = () => {
            inputResizeRef.current = false
            window.removeEventListener('mousemove', onMove)
            window.removeEventListener('mouseup', onUp)
          }
          window.addEventListener('mousemove', onMove)
          window.addEventListener('mouseup', onUp)
        }}
      />

      {/* 输入框（文件可直接拖进来放下，与点回形针等价） */}
      <div className="p-3 bg-surface border-t border-border relative" {...attachments.zoneProps('input')} {...attachments.pasteProps}>
        <DropMask {...attachments.dropState('input')} label={t('chat.dropToAdd')} />
        {/* 附件预览列表（与群视界世界对话共用同一组件） */}
        <AttachmentChips items={attachments.items} onRemove={attachments.remove} errorText={t('chat.uploadFailed')} />

        {/* @提及 自动补全下拉 */}
        {mentionActive && mentionFiltered.length > 0 && (
          <MenuPanel className="absolute bottom-full left-3 right-3 mb-1 max-h-48 overflow-y-auto z-modal">
            {mentionFiltered.map((m, i) => (
              <MenuItem
                key={`${m.type}:${m.id}`}
                active={i === mentionIdx}
                onClick={() => insertMention(m.name)}
                className="flex items-center gap-2 py-2 text-sm"
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${getStateDotColor(m.state)}`} />
                <span className="font-medium">{m.name}</span>
                <span className="text-textMuted text-xs ml-auto">
                  {m.type === 'ai' ? <><Bot size={12} className="inline" /> AI</> : <User size={12} className="inline" />}
                </span>
              </MenuItem>
            ))}
          </MenuPanel>
        )}

        {replyTo && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-t-xl border border-border bg-surface/50 text-xs">
            <div className="w-0.5 h-8 bg-primary-400 rounded-full shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-primary-400 font-medium truncate">回复 @{replyTo.sender_name}</div>
              <div className="text-textMuted truncate">{replyTo.content}</div>
            </div>
            <button onClick={() => setReplyTo(null)} className="shrink-0 p-1 rounded-control hover:bg-elevated text-textMuted hover:text-textPrimary transition-colors">
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
          onAutoHeight={(ah) => {
            setInputHeight(prev => {
              const cur = prev || 0
              return cur + ah < 44 ? 44 - ah : cur
            })
          }}
        />
      </div>

      {/* 资料卡 */}
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
