import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client'
import { getWsUrl } from '../utils/platform'
import { safeParse } from '../utils/result'
import { notifyRequestsChanged } from './usePendingRequests'
import { CHAT_REFRESH_EVENT } from '../constants'

/**
 * 站内新消息弹窗的全局连接（唯一实现）
 *
 * 与 ChatView 那条会话连接是并列的两条：
 * - 会话连接只订阅当前会话，负责消息流与输入/在线状态；
 * - 这条常驻连接不订阅任何会话，只收后端按"通知范围"推来的 push（群消息、私聊消息、
 *   审批结果）与用户级事件（好友通知、群邀请卡片、维护公告），交给右下角浮层。
 *
 * 为什么要分开：一条 WS 只能订阅一个会话，混在一起就会"进了群聊就收不到私聊弹窗"。
 */

/** 后端 kind → 前端文案模板。后端只说事实，中文/英文/日文在这里出。 */
export type NotificationKind =
  | 'group_message'
  | 'dm_message'
  | 'announcement'
  | 'group_invite_card'
  | 'friend_request'
  | 'friend_accepted'
  | 'friend_rejected'
  | 'group_join_requested'
  | 'group_join_approved'
  | 'group_join_rejected'
  | 'group_invite_approved'
  | 'group_invite_denied'
  | 'group_invite_accepted'
  | 'group_invite_declined'
  | 'system'

export interface NotificationItem {
  id: string
  kind: NotificationKind
  /** 场所：群名 / 对方名字 */
  place: string | null
  /** 说话的人（群消息里"谁说的"） */
  sender: string | null
  /** 正文摘要 */
  preview: string | null
  avatarUrl: string | null
  /** 点击跳转的路由（没有就只是个提示） */
  to: string | null
  receivedAt: number
}

/** 浮层最多同时显示几条：多了就顶掉最旧的，免得回到电脑前积一屏 */
const MAX_ITEMS = 4
const RECONNECT_MS = 5000
const STORAGE_KEY = 'inapp_notifications_enabled'

/** 会话名/免打扰缓存：弹窗要立刻有名有姓，不能等下一次列表请求 */
interface SessionCache {
  groupNames: Record<string, string>
  groupAvatars: Record<string, string | null>
  mutedGroups: Set<number>
  dmPeers: Record<string, string>
  mutedDms: Set<string>
}

const EMPTY_CACHE: SessionCache = {
  groupNames: {}, groupAvatars: {}, mutedGroups: new Set(),
  dmPeers: {}, mutedDms: new Set(),
}

/** 免打扰判断：群看 dnd_until，私信看我这一侧的 dnd */
async function loadSessionCache(): Promise<SessionCache> {
  const [groups, sessions] = await Promise.all([
    api.get<any[]>('/groups').catch(() => []),
    api.get<any[]>('/dm/sessions').catch(() => []),
  ])
  const cache: SessionCache = {
    groupNames: {}, groupAvatars: {}, mutedGroups: new Set(),
    dmPeers: {}, mutedDms: new Set(),
  }
  for (const g of groups || []) {
    cache.groupNames[g.id] = g.name
    cache.groupAvatars[g.id] = g.avatar_url ?? null
    if (g.dnd_until) cache.mutedGroups.add(g.id)
  }
  for (const s of sessions || []) {
    cache.dmPeers[s.session_id] = s.partner_name || s.peer_name || s.session_id
    if (s.my_dnd_until) cache.mutedDms.add(s.session_id)
  }
  return cache
}

/** 群消息/私信消息 → 弹窗条目（其余 kind 的字段直接来自后端） */
function toItem(kind: NotificationKind, data: any, cache: SessionCache): NotificationItem | null {
  const id = `${kind}-${data.conversation_id ?? data.group_id ?? data.session_id ?? ''}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  const base = { id, kind, receivedAt: Date.now(), avatarUrl: null as string | null }

  if (kind === 'group_message') {
    const gid = data.group_id ?? data.conversation_id
    if (cache.mutedGroups.has(Number(gid))) return null
    return {
      ...base,
      place: cache.groupNames[gid] ?? null,
      sender: data.message?.sender_name ?? null,
      preview: summarize(data.message),
      avatarUrl: data.message?.sender_avatar_url ?? cache.groupAvatars[gid] ?? null,
      to: gid ? `/chat/gm/${gid}` : null,
    }
  }
  if (kind === 'dm_message' || kind === 'group_invite_card') {
    const sid = data.session_id ?? data.conversation_id
    if (cache.mutedDms.has(sid)) return null
    return {
      ...base,
      place: cache.dmPeers[sid] ?? null,
      sender: null,
      preview: summarize(data.message) ?? (kind === 'group_invite_card' ? data.message?.attachments?.[0]?.group_name ?? null : null),
      avatarUrl: data.message?.sender_avatar_url ?? null,
      to: sid ? `/chat/dm/${sid}` : null,
    }
  }

  const groupId = data.group_id
  const place = data.group_name ?? cache.groupNames[groupId] ?? null
  const to = groupId ? `/chat/gm/${groupId}` : null
  switch (kind) {
    case 'announcement':
      return { ...base, place, sender: null, preview: data.content ?? null, to }
    case 'group_join_requested':
      return { ...base, place, sender: data.actor_name ?? null, preview: null, to: '/list?tab=requests' }
    case 'group_join_approved':
    case 'group_join_rejected':
      return { ...base, place, sender: null, preview: null, to }
    case 'group_invite_approved':
    case 'group_invite_denied':
    case 'group_invite_accepted':
    case 'group_invite_declined':
      return { ...base, place, sender: data.target_name ?? null, preview: null, to }
    case 'friend_request':
    case 'friend_accepted':
    case 'friend_rejected':
      return { ...base, place: null, sender: data.actor_name ?? null, preview: data.message ?? null, to: '/list?tab=requests' }
    case 'system':
      return { ...base, place: null, sender: null, preview: data.text ?? null, to: null }
    default:
      return null
  }
}

/** 消息摘要：附件消息也要说得出话，别弹出一条空白 */
function summarize(message: any): string | null {
  if (!message) return null
  const text = typeof message.content === 'string' ? message.content.trim() : ''
  if (text) return text.length > 80 ? `${text.slice(0, 80)}...` : text
  if (Array.isArray(message.attachments) && message.attachments.length > 0) return null
  return null
}

/** kind 由事件类型与 payload 共同决定（后端 push 已带 kind，用户级事件在这里归类） */
function resolveKind(payload: any): NotificationKind | null {
  if (payload.type === 'push') return (payload.data?.kind as NotificationKind) ?? null
  if (payload.type === 'announcement') return 'announcement'
  if (payload.type === 'maintenance_update') return 'system'
  if (payload.type === 'friend_notification') {
    const event = payload.data?.event
    if (event === 'request_received') return 'friend_request'
    if (event === 'request_accepted') return 'friend_accepted'
    if (event === 'request_rejected') return 'friend_rejected'
    return null
  }
  if (payload.type === 'message' || payload.type === 'ai_response') {
    // 群邀请卡片是一条 DM：单独归类，文案说"收到群聊邀请"而不是"一条新消息"
    if (payload.data?.message_type === 'group_invitation') return 'group_invite_card'
    return payload.data?.conversation_type === 'dm' ? 'dm_message' : 'group_message'
  }
  return null
}

export interface NotificationFeed {
  items: NotificationItem[]
  dismiss: (id: string) => void
  dismissAll: () => void
}

/** 常驻通知连接 + 待显示的弹窗队列 */
export function useNotificationSocket(): NotificationFeed {
  const [items, setItems] = useState<NotificationItem[]>([])
  const cacheRef = useRef<SessionCache>(EMPTY_CACHE)
  const location = useLocation()
  // location 会被 onmessage 闭包捕获，用 ref 取"此刻正在看哪个会话"
  const pathRef = useRef(location.pathname)
  pathRef.current = location.pathname

  useEffect(() => {
    let closed = false
    let ws: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    const enabled = () => localStorage.getItem(STORAGE_KEY) !== 'false'
    const currentPath = () => pathRef.current

    /** 当前正在看的会话不再弹（人已经在里面了） */
    const isViewing = (item: NotificationItem) => !!item.to && currentPath().startsWith(item.to.split('?')[0])

    const push = (item: NotificationItem) => {
      if (!enabled() || isViewing(item)) return
      // 标签页在后台时交给系统通知，不积一屏站内浮层
      if (document.hidden) return
      setItems((prev) => [...prev, item].slice(-MAX_ITEMS))
    }

    const handle = (payload: any) => {
      if (payload.type === 'requests_changed') {
        notifyRequestsChanged()
        return
      }
      const kind = resolveKind(payload)
      if (!kind) return
      // push 把消息裹在 data.message 里；直接推来的 message 事件消息就在 data 本身。
      // 统一成 data.message，让 toItem 只有一套读法。
      const message = payload.type === 'push' ? payload.data?.message : payload.data
      const item = toItem(kind, { ...payload.data, message }, cacheRef.current)
      if (item) push(item)
    }

    const refreshCache = () => {
      loadSessionCache().then((cache) => { cacheRef.current = cache }).catch(() => {})
    }

    const subscribe = () => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'notifications_subscribe' }))
      }
    }

    const connect = () => {
      const token = localStorage.getItem('access_token')
      if (!token || closed) return
      try {
        ws = new WebSocket(getWsUrl(token))
      } catch {
        retryTimer = setTimeout(connect, RECONNECT_MS)
        return
      }
      ws.onopen = () => { subscribe(); refreshCache() }
      ws.onmessage = (event) => {
        const parsed = safeParse<any>(event.data)
        if (!parsed.ok) return
        const payload = parsed.value
        if (payload.type === 'ping') {
          ws?.send(JSON.stringify({ type: 'pong' }))
          return
        }
        // 心跳靠"收到任何数据"续命，pong 之外的消息不额外回执
        handle(payload)
      }
      ws.onclose = () => {
        if (closed) return
        if (retryTimer) clearTimeout(retryTimer)
        retryTimer = setTimeout(connect, RECONNECT_MS)
      }
    }

    refreshCache()
    connect()

    // 群/私信列表变了（新建群、新私信）→ 重算缓存并重新登记通知范围
    const onRefresh = () => { refreshCache(); subscribe() }
    window.addEventListener('groupListRefresh', onRefresh)
    window.addEventListener(CHAT_REFRESH_EVENT, onRefresh)

    return () => {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      window.removeEventListener('groupListRefresh', onRefresh)
      window.removeEventListener(CHAT_REFRESH_EVENT, onRefresh)
      ws?.close()
    }
  }, [])

  return {
    items,
    dismiss: (id: string) => setItems((prev) => prev.filter((i) => i.id !== id)),
    dismissAll: () => setItems([]),
  }
}

export { STORAGE_KEY as IN_APP_NOTIFICATION_KEY }
