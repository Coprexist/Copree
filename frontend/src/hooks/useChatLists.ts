/**
 * 会话列表（/groups + /dm/sessions）的唯一来源
 *
 * 侧栏要它的全部、标签页未读计数要它的数字、站内弹窗要它的名字头像与免打扰——三个消费者
 * 要的是同一份东西，而一条新消息会同时惊动三家。各自去拉就是每条消息三倍的请求，而这两个
 * 接口都不便宜（实测：16 条私信的会话列表 69 条 SQL、1.2 秒）。所以：
 * - 列表只在这里拉，同一时刻的多次调用合并成一次往返；
 * - 新消息**就地改这一份**（预览、时间、未读 +1），不重拉；
 * - 已读落库成功后把那一项清零；
 * - 只有「列表本身可能变了」（新建群/新私信/自己发过消息）才真的重拉。
 */
import { useSyncExternalStore } from 'react'
import { api } from '../api/client'
import { conversationKey, readingKey } from './useReadingConversation'

export interface ChatLists {
  groups: any[]
  sessions: any[]
  /** 至少成功拉过一次；没过之前别拿空列表当「没有会话」 */
  loaded: boolean
}

let state: ChatLists = { groups: [], sessions: [], loaded: false }
const listeners = new Set<() => void>()

function setState(next: ChatLists) {
  state = next
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** 订阅会话列表（侧栏渲染、未读计数都用它） */
export function useChatLists(): ChatLists {
  return useSyncExternalStore(subscribe, () => state)
}

let inflight: Promise<ChatLists> | null = null

/** 拉一次会话列表；同一时刻的多次调用共用同一次往返 */
export function refreshChatLists(): Promise<ChatLists> {
  if (inflight) return inflight
  inflight = Promise.all([
    api.get<any[]>('/groups').catch(() => null),
    api.get<any[]>('/dm/sessions').catch(() => null),
  ])
    .then(([groups, sessions]) => {
      // 失败的那一半保持原样：宁可显示旧数据，也别把侧栏清空
      setState({
        groups: groups ?? state.groups,
        sessions: sessions ?? state.sessions,
        loaded: state.loaded || groups != null || sessions != null,
      })
      return state
    })
    .finally(() => { inflight = null })
  return inflight
}

/** 同一条消息会从两条 ws（会话连接 + 常驻通知连接）各到一次，按会话记住最后应用的 id 去重 */
const lastApplied = new Map<string, number>()

export interface IncomingMessage {
  conversationType: 'group' | 'dm'
  conversationId: number | string
  messageId?: number
  preview: string | null
  at: string | null
  mentionedMe?: boolean
}

/** 新消息就地落到列表上：预览、时间、未读 +1（我正贴在那个会话底部时保持 0） */
export function applyIncomingMessage(ev: IncomingMessage, retried = false): void {
  if (!state.loaded) {
    // 列表还没到（页面刚打开）：等这一次拉取落地再改，否则这条就白丢了。只重试一次
    if (!retried) refreshChatLists().then(() => { if (state.loaded) applyIncomingMessage(ev, true) })
    return
  }
  const key = conversationKey(ev.conversationType, ev.conversationId)
  if (ev.messageId != null) {
    const prev = lastApplied.get(key)
    if (prev != null && ev.messageId <= prev) return
    lastApplied.set(key, ev.messageId)
  }
  const reading = readingKey() === key

  if (ev.conversationType === 'group') {
    const id = Number(ev.conversationId)
    if (!state.groups.some((g) => g.id === id)) { refreshChatLists(); return }
    setState({ ...state, groups: state.groups.map((g) => g.id === id ? {
      ...g,
      last_message_preview: ev.preview ?? g.last_message_preview,
      last_message_at: ev.at ?? g.last_message_at,
      unread_count: reading ? 0 : (g.unread_count || 0) + 1,
      has_mention: reading ? false : (g.has_mention || ev.mentionedMe === true),
    } : g) })
    return
  }
  const sid = String(ev.conversationId)
  if (!state.sessions.some((s) => s.session_id === sid)) { refreshChatLists(); return }
  setState({ ...state, sessions: state.sessions.map((s) => s.session_id === sid ? {
    ...s,
    last_message_preview: ev.preview ?? s.last_message_preview,
    last_message_at: ev.at ?? s.last_message_at,
    unread_count: reading ? 0 : (s.unread_count || 0) + 1,
  } : s) })
}

/** 已读落库成功后把那一项清零（红数泡的来源就是这个 unread_count） */
export function markConversationRead(type: 'group' | 'dm', id: number | string): void {
  if (type === 'group') {
    const gid = Number(id)
    if (!state.groups.some((g) => g.id === gid && (g.unread_count > 0 || g.has_mention))) return
    setState({ ...state, groups: state.groups.map((g) => g.id === gid
      ? { ...g, unread_count: 0, has_mention: false } : g) })
    return
  }
  const sid = String(id)
  if (!state.sessions.some((s) => s.session_id === sid && s.unread_count > 0)) return
  setState({ ...state, sessions: state.sessions.map((s) => s.session_id === sid
    ? { ...s, unread_count: 0 } : s) })
}

/** 就地改一项（置顶、头像、在线人数这些局部变化，不必为它重拉整张表） */
export function updateGroup(id: number, patch: Record<string, any>): void {
  if (!state.groups.some((g) => g.id === id)) return
  setState({ ...state, groups: state.groups.map((g) => g.id === id ? { ...g, ...patch } : g) })
}

export function updateSession(sessionId: string, patch: Record<string, any>): void {
  if (!state.sessions.some((s) => s.session_id === sessionId)) return
  setState({ ...state, sessions: state.sessions.map((s) => s.session_id === sessionId ? { ...s, ...patch } : s) })
}
