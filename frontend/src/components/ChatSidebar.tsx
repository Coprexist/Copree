import { memo, useState, useEffect, useRef, useMemo, useCallback, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Plus, BellOff, Menu, UserPlus, Users, Bot, Globe, ShieldAlert, MessageCircle, Inbox, Pin, ChevronDown, ChevronRight } from 'lucide-react'
import { EmptyState, MenuPanel, MenuItem } from './ui'
import { getStateDotColor, CHAT_REFRESH_EVENT } from '../constants'
import { formatRelativeTime } from '../utils/time'
import { GroupAvatarGroup, thumbUrl } from './GroupAvatar'
import { getStatusTextStyle, BG_SURFACE_LIGHT, BG_SURFACE_DARK } from '../utils/statusColor'
import { useTheme } from '../context/ThemeContext'
import { useLang, useT } from '../i18n/I18nContext'

/** URL 正则（匹配 http/https 链接） */
const URL_RE = /(https?:\/\/[^\s<]+[^\s<.,;:!?)}\]'"])/g

/** 预览文本组件：自动识别 URL 并赋予链接颜色 */
function PreviewText({ text, placeholder }: { text: string | null; placeholder: string }) {
  if (!text) return <span className="truncate block">{placeholder}</span>
  const parts: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  URL_RE.lastIndex = 0
  while ((match = URL_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    parts.push(
      <span key={match.index} className="text-primary-500 dark:text-primary-400">{match[0]}</span>
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }
  return <span className="truncate block">{parts}</span>
}

interface Group {
  id: number
  name: string
  unread_count: number
  has_mention: boolean
  last_message_preview: string | null
  last_message_at: string | null
  dnd_until: string | null
  member_avatars: string[]
  avatar_mode?: string
  avatar_url?: string | null
  include_ai_in_avatar?: boolean
  is_federated?: boolean
  is_pinned?: boolean
}

interface DMSession {
  session_id: string
  partner: {
    id: number
    name: string
    type: string
    state: string | null
    avatar_url: string | null
    status_text: string | null
    status_color: string | null
  }
  last_message_preview: string | null
  last_message_at: string | null
  unread_count: number
  is_federated?: boolean
  is_pinned?: boolean
  is_special_care?: boolean
}

interface ChatSidebarProps {
  activeGroupId: number | null
  activeSessionId: string | null
  onCreateGroup: () => void
  onAddFriend: () => void
  openDrawer: () => void
  /** 移动端选中对话后隐藏侧边栏 */
  hideOnMobile: boolean
  /** 移动端返回当前对话（侧边栏作为 overlay 时） */
  onMobileBack?: () => void
  /** 移动端全屏覆盖模式 */
  mobileFullscreen?: boolean
}

/** 从 localStorage 读取折叠状态，默认展开 */
function getCollapsed(key: string): boolean {
  try {
    return localStorage.getItem(`sidebar_collapsed_${key}`) === 'true'
  } catch {
    return false
  }
}

function setCollapsed(key: string, val: boolean) {
  try {
    localStorage.setItem(`sidebar_collapsed_${key}`, val ? 'true' : 'false')
  } catch { /* ignore */ }
}

const ChatSidebar = memo(function ChatSidebar({
  activeGroupId,
  activeSessionId,
  onCreateGroup,
  onAddFriend,
  openDrawer,
  hideOnMobile,
  onMobileBack,
  mobileFullscreen,
}: ChatSidebarProps) {
  const [groups, setGroups] = useState<Group[]>([])
  const [dmSessions, setDmSessions] = useState<DMSession[]>([])
  const [showPlusMenu, setShowPlusMenu] = useState(false)
  const [pinnedCollapsed, setPinnedCollapsed] = useState(() => getCollapsed('pinned'))
  const [groupsCollapsed, setGroupsCollapsed] = useState(() => getCollapsed('groups'))
  const [dmCollapsed, setDmCollapsed] = useState(() => getCollapsed('dm'))
  const navigate = useNavigate()

  const loadGroups = useCallback(() => api.get('/groups').then(setGroups).catch(() => {}), [])
  const loadDMSessions = useCallback(() => api.get('/dm/sessions').then(setDmSessions).catch(() => {}), [])

  // 初始加载
  useEffect(() => {
    loadGroups()
    window.addEventListener('groupListRefresh', loadGroups)
    loadDMSessions()
    return () => window.removeEventListener('groupListRefresh', loadGroups)
  }, [loadGroups, loadDMSessions])

  // 活跃对话变化时刷新（延迟等 mark-as-read 完成）
  useEffect(() => {
    const timer = setTimeout(() => {
      loadGroups()
      loadDMSessions()
    }, 300)
    return () => clearTimeout(timer)
  }, [activeGroupId, activeSessionId, loadGroups, loadDMSessions])

  // 用 ref 持有最新 active ID，避免 chat-refresh 事件监听器随对话切换而重建
  const activeGroupIdRef = useRef(activeGroupId)
  activeGroupIdRef.current = activeGroupId
  const activeSessionIdRef = useRef(activeSessionId)
  activeSessionIdRef.current = activeSessionId

  // 去抖 ref：防止高频 chat-refresh 导致请求风暴
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reloadDebounced = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      loadGroups()
      loadDMSessions()
    }, 500)
  }, [loadGroups, loadDMSessions])

  // chat-refresh 事件 — 监听器只注册一次（[] 依赖），通过 ref 读取最新 ID
  useEffect(() => {
    const handler = (e: CustomEvent) => {
      const t = e.detail?.type
      if (t === 'dm_notification' || t === 'unread_update' || t === 'message_sent') {
        reloadDebounced()
      }
    }
    window.addEventListener(CHAT_REFRESH_EVENT, handler as EventListener)

    // 置顶/取消置顶即时更新（不触发全量请求）
    const pinHandler = (e: Event) => {
      const d = (e as CustomEvent).detail
      if (d?.groupId) {
        setGroups(prev => prev.map(g => g.id === d.groupId ? { ...g, is_pinned: d.isPinned } : g))
      }
      if (d?.sessionId) {
        setDmSessions(prev => prev.map(s => s.session_id === d.sessionId ? { ...s, is_pinned: d.isPinned } : s))
      }
    }
    window.addEventListener('groupPinChanged', pinHandler)

    // 头像更新即时刷新
    const avatarHandler = (e: Event) => {
      const d = (e as CustomEvent).detail
      if (d?.groupId) {
        setGroups(prev => prev.map(g => g.id === d.groupId ? { ...g, avatar_url: d.avatar_url, avatar_mode: d.avatar_mode || 'custom' } : g))
      }
    }
    window.addEventListener('groupAvatarChanged', avatarHandler)

    return () => {
      window.removeEventListener(CHAT_REFRESH_EVENT, handler as EventListener)
      window.removeEventListener('groupPinChanged', pinHandler)
      window.removeEventListener('groupAvatarChanged', avatarHandler)
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [reloadDebounced])

  // 保存折叠状态
  useEffect(() => { setCollapsed('pinned', pinnedCollapsed) }, [pinnedCollapsed])
  useEffect(() => { setCollapsed('groups', groupsCollapsed) }, [groupsCollapsed])
  useEffect(() => { setCollapsed('dm', dmCollapsed) }, [dmCollapsed])

  // 排序函数：按 last_message_at 降序（最新在前），无时间戳的排末尾
  const sortByTime = (a: any, b: any) => {
    const ta = a.last_message_at ? new Date(a.last_message_at).getTime() : 0
    const tb = b.last_message_at ? new Date(b.last_message_at).getTime() : 0
    return tb - ta
  }

  // ── 分组数据 ──

  const regularGroups = useMemo(() => groups
    .filter((g: any) => !g.name?.startsWith('DM:'))
    .sort(sortByTime)
  , [groups])

  const sortedDMSessions = useMemo(() => [...dmSessions].sort(sortByTime), [dmSessions])

  // 置顶区：混合群聊和私信中 is_pinned 的项目，按时间排序
  const pinnedItems = useMemo(() => {
    const pinnedGroups = regularGroups.filter(g => g.is_pinned)
    const pinnedDMs = sortedDMSessions.filter(s => s.is_pinned)
    const all = [
      ...pinnedGroups.map(g => ({ kind: 'group' as const, data: g, lastMessageAt: g.last_message_at })),
      ...pinnedDMs.map(s => ({ kind: 'dm' as const, data: s, lastMessageAt: s.last_message_at })),
    ]
    all.sort((a, b) => sortByTime(a, b))
    return all
  }, [regularGroups, sortedDMSessions])

  // 非置顶的群聊
  const unpinnedGroups = useMemo(() => regularGroups.filter(g => !g.is_pinned), [regularGroups])
  // 非置顶的私信
  const unpinnedDMs = useMemo(() => sortedDMSessions.filter(s => !s.is_pinned), [sortedDMSessions])

  // 未读总数
  const groupsUnreadTotal = useMemo(() =>
    unpinnedGroups.reduce((acc, g) => acc + g.unread_count, 0)
  , [unpinnedGroups])
  const dmUnreadTotal = useMemo(() =>
    unpinnedDMs.reduce((acc, s) => acc + s.unread_count, 0)
  , [unpinnedDMs])

  const lang = useLang()
  const t = useT()
  const { theme } = useTheme()

  /** DM 头像组件 */
  const DmAvatar = ({ session }: { session: DMSession }) => {
    const p = session.partner
    return (
      <div className="w-9 h-9 rounded-full relative shrink-0">
        {p.avatar_url ? (
          <>
            <div className={`absolute inset-px rounded-full bg-gradient-to-bl ${
              p.type === 'system' ? 'from-rose-400 to-rose-600' : 'from-teal-400 to-teal-600'
            }`} />
            <img src={thumbUrl(p.avatar_url) || p.avatar_url} alt="" className="relative w-full h-full rounded-full object-cover" loading="lazy" decoding="async" />
          </>
        ) : (
          <div className={`w-full h-full rounded-full bg-gradient-to-bl flex items-center justify-center ${
            p.type === 'system' ? 'from-rose-400 to-rose-600' : 'from-teal-400 to-teal-600'
          }`}>
            {p.type === 'system' ? (
              <ShieldAlert size={16} className="text-white" />
            ) : (
              <span className="text-xs font-bold text-white">{p.name?.charAt(0)?.toUpperCase() || '?'}</span>
            )}
          </div>
        )}
        {p.type !== 'system' && (
          <span data-mv-force className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-surface ${getStateDotColor(p.state)}`} />
        )}
      </div>
    )
  }

  const nothingSelected = !activeGroupId && !activeSessionId

  // ── 渲染单个群聊条目 ──
  const renderGroupItem = (g: Group, isActive: boolean) => (
    <button
      key={`group-${g.id}`}
      onClick={() => {
        if (g.id === activeGroupId && mobileFullscreen) {
          onMobileBack?.()
        } else {
          navigate(`/chat/gm/${g.id}`)
        }
      }}
      className={`w-full text-left px-3 py-2 text-sm transition-all duration-150 ${
        isActive
          ? 'bg-primary-500/15 text-primary-600 dark:text-primary-300 border-l-2 border-primary-400'
          : 'hover:bg-elevated text-textSecondary border-l-2 border-transparent'
      }`}
    >
      <div className="flex items-center gap-2.5">
        <GroupAvatarGroup g={g} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <div className="font-medium flex items-center gap-1 min-w-0">
              {g.is_pinned && <Pin size={10} className="text-primary-400 shrink-0" />}
              {g.is_federated && <Globe size={11} className="text-primary-400 shrink-0" />}
              <span className="truncate block">{g.name}</span>
            </div>
            {g.unread_count > 0 && (
              <span className={`shrink-0 ml-1 min-w-[18px] h-[18px] rounded-full flex items-center justify-center text-3xs font-bold text-white ${
                g.has_mention
                  ? 'bg-rose-500 shadow-sm shadow-rose-500/30'
                  : 'bg-primary-500/80'
              }`}>
                {g.unread_count > 99 ? '99+' : g.unread_count}
              </span>
            )}
          </div>
          <div className="text-2xs text-textMuted mt-0.5 flex items-center gap-1 min-w-0">
            {g.dnd_until && <BellOff size={10} className="text-rose-400 shrink-0" />}
            {/* 免打扰照显：被点名是"叫我"，跟"别拿常规消息打扰我"不是一回事 */}
            {g.has_mention && (
              <span className="text-rose-400 font-medium shrink-0">{t('chatlist.atYou')}</span>
            )}
            <span className="min-w-0 flex-1" style={{ display: 'block' }}>
              <PreviewText text={g.last_message_preview} placeholder={t('chatlist.noMessages')} />
            </span>
            {g.last_message_at && (
              <span className="shrink-0">{formatRelativeTime(g.last_message_at, lang)}</span>
            )}
          </div>
        </div>
      </div>
    </button>
  )

  // ── 渲染单个私信条目 ──
  const renderDMItem = (s: DMSession, isActive: boolean) => (
    <button
      key={`dm-${s.session_id}`}
      onClick={() => {
        if (s.session_id === activeSessionId && mobileFullscreen) {
          onMobileBack?.()
        } else {
          navigate(`/chat/dm/${s.session_id}`)
        }
      }}
      className={`w-full text-left px-3 py-2 text-sm transition-all duration-150 ${
        isActive
          ? 'bg-primary-500/15 text-primary-600 dark:text-primary-300 border-l-2 border-primary-400'
          : 'hover:bg-elevated text-textSecondary border-l-2 border-transparent'
      }`}
    >
      <div className="flex items-center gap-2.5">
        <DmAvatar session={s} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <div className="font-medium flex items-center gap-1.5 min-w-0">
              {s.is_pinned && <Pin size={10} className="text-primary-400 shrink-0" />}
              {s.partner.type === 'system' && <ShieldAlert size={11} className="shrink-0 text-rose-400" />}
              {s.partner.type === 'ai' && <Bot size={11} className="shrink-0 text-mint-400" />}
              {s.is_federated && <Globe size={11} className="text-primary-400 shrink-0" />}
              <span className="truncate">{s.partner.name}</span>
              {s.partner.status_text && (
                <span className="text-2xs font-medium truncate" style={s.partner.status_color
                  ? getStatusTextStyle(s.partner.status_color, theme === 'dark' ? BG_SURFACE_DARK : BG_SURFACE_LIGHT)
                  : undefined}>
                  · {s.partner.status_text}
                </span>
              )}
            </div>
            {s.unread_count > 0 && (
              <span className="shrink-0 ml-1 min-w-[18px] h-[18px] rounded-full flex items-center justify-center text-3xs font-bold text-white bg-primary-500/80">
                {s.unread_count > 99 ? '99+' : s.unread_count}
              </span>
            )}
          </div>
          <div className="text-2xs text-textMuted mt-0.5 flex items-center gap-1 min-w-0">
            <span className="min-w-0 flex-1" style={{ display: 'block' }}>
              <PreviewText text={s.last_message_preview} placeholder={t('chatlist.noMessages')} />
            </span>
            {s.last_message_at && (
              <span className="shrink-0">{formatRelativeTime(s.last_message_at, lang)}</span>
            )}
          </div>
        </div>
      </div>
    </button>
  )

  // ── 折叠区域标题组件 ──
  const CollapsibleHeader = ({
    label,
    collapsed,
    onToggle,
    unreadCount,
  }: {
    label: string
    collapsed: boolean
    onToggle: () => void
    unreadCount?: number
  }) => (
    <div className="flex items-center px-3 py-1 group">
      <button
        onClick={onToggle}
        className="flex items-center gap-1 text-3xs font-semibold uppercase tracking-wider text-textMuted hover:text-textSecondary transition-colors"
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
        {label}
        {unreadCount !== undefined && unreadCount > 0 && (
          <span className="ml-1 min-w-[14px] h-[14px] rounded-full bg-primary-500/70 text-white flex items-center justify-center text-[9px] font-bold">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>
    </div>
  )

  return (
    <div className={`w-full bg-surface border-r border-border shrink-0 flex-col flex h-full ${
      hideOnMobile && !nothingSelected ? 'hidden' : 'flex'
    } md:flex`}>
      {/* 标题 */}
      <div className="px-3 h-14 border-b border-border font-medium text-sm flex items-center justify-between text-textPrimary shrink-0">
        <div className="flex items-center gap-2">
          <button
            onClick={openDrawer}
            className="icon-btn-sm md:hidden text-textSecondary"
            title={t('chatlist.menu')}
          >
            <Menu size={18} />
          </button>
          <span>{t('chatlist.chat')}</span>
        </div>
        <div className="relative">
          <button
            onClick={() => setShowPlusMenu(!showPlusMenu)}
            className="p-1 rounded-control hover:bg-elevated text-textMuted hover:text-primary-400 transition-colors"
            title={t('chatlist.createNewGroup')}
          >
            <Plus size={16} />
          </button>
          {showPlusMenu && (
            <>
              <div className="fixed inset-0 z-drawer" onClick={() => setShowPlusMenu(false)} />
              <MenuPanel className="absolute right-0 top-full mt-1 w-36 py-1 z-modal">
                <MenuItem
                  onClick={() => { setShowPlusMenu(false); onCreateGroup() }}
                  className="flex items-center gap-2.5 px-3.5 py-2.5 text-sm"
                >
                  <Users size={15} />
                  {t('chatlist.createGroup')}
                </MenuItem>
                <MenuItem
                  onClick={() => { setShowPlusMenu(false); onAddFriend() }}
                  className="flex items-center gap-2.5 px-3.5 py-2.5 text-sm"
                >
                  <UserPlus size={15} />
                  {t('list.add')}
                </MenuItem>
              </MenuPanel>
            </>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {/* ── 置顶区 ── */}
        {pinnedItems.length > 0 && (
          <>
            <CollapsibleHeader
              label={t('chatlist.pinned')}
              collapsed={pinnedCollapsed}
              onToggle={() => setPinnedCollapsed(v => !v)}
            />
            {!pinnedCollapsed && (
              <div>
                {pinnedItems.map(item => {
                  if (item.kind === 'group') {
                    const g = item.data as Group
                    return renderGroupItem(g, g.id === activeGroupId)
                  } else {
                    const s = item.data as DMSession
                    return renderDMItem(s, s.session_id === activeSessionId)
                  }
                })}
              </div>
            )}
          </>
        )}

        {/* ── 群聊区 ── */}
        <CollapsibleHeader
          label={t('chatlist.chat')}
          collapsed={groupsCollapsed}
          onToggle={() => setGroupsCollapsed(v => !v)}
          unreadCount={groupsUnreadTotal}
        />
        {!groupsCollapsed && (
          unpinnedGroups.length === 0 ? (
            <div className="px-3 pt-2">
              <EmptyState icon={MessageCircle} title="暂无群聊" description="新建一个群聊开始聊天吧" />
            </div>
          ) : (
            unpinnedGroups.map(g => renderGroupItem(g, g.id === activeGroupId))
          )
        )}

        {/* ── 私信区 ── */}
        <CollapsibleHeader
          label={t('chatlist.dm')}
          collapsed={dmCollapsed}
          onToggle={() => setDmCollapsed(v => !v)}
          unreadCount={dmUnreadTotal}
        />
        {!dmCollapsed && (
          unpinnedDMs.length === 0 ? (
            <div className="px-3 pt-2">
              <EmptyState icon={Inbox} title="暂无私信" description="和好友或 AI 开始私聊吧" />
            </div>
          ) : (
            unpinnedDMs.map(s => renderDMItem(s, s.session_id === activeSessionId))
          )
        )}
      </div>
    </div>
  )
})
export default ChatSidebar
