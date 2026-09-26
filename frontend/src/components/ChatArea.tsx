import { useState, useEffect, useRef } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { api } from '../api/client'
import ChatView from './ChatView'
import ChatSidebar from './ChatSidebar'
import DMChatView from './DMChatView'
import GroupSettingsPanel from './GroupSettingsPanel'
import { GroupAvatarHeader, thumbUrl } from './GroupAvatar'
import ProfileCard from './ProfileCard'
import SearchOverlay from './SearchOverlay'
import { Bell, BellOff, UserPlus, Settings, ArrowLeft, Bot, User, Globe, X, Check, Users, AlertTriangle } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { useResizableSidebar } from '../hooks/useResizableSidebar'
import { isEmbedded } from '../embed/bridge'
import { Dialog } from './ui'

/** 嵌入模式（?embed=1）：隐藏聊天列表侧边栏，只渲染对话区，导航由宿主提供 */
const EMBED = isEmbedded()

interface Group {
  id: number
  name: string
  owner_type: string
  owner_id: number
  is_vector_accelerated: boolean
  announcement: string | null
  speak_limit_per_minute: number
  speak_limit_window_seconds: number
  is_paused: boolean
  is_pinned?: boolean
  concurrent_ai_limit: number
  my_role: string
  unread_count: number
  has_mention: boolean
  last_message_preview: string | null
  dnd_until: string | null
  created_at: string | null
  member_count: number
  online_count: number
  avatar_mode?: string
  avatar_url?: string | null
  include_ai_in_avatar?: boolean
  is_federated?: boolean
  // 发现与入群三开关（GET /groups 已返回；可选是为了兼容旧缓存数据）
  searchable?: boolean
  auto_approve_join?: boolean
  approve_invites?: boolean
}

interface ChatAreaProps {
  groupId: number | null
  dmSessionId: string | null
}

export default function ChatArea({ groupId, dmSessionId }: ChatAreaProps) {
  const t = useT()
/** 群聊头部头像组件 */
  const [groups, setGroups] = useState<Group[]>([])
  const [showCreateGroup, setShowCreateGroup] = useState(false)
  const [showInvite, setShowInvite] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [profileGroup, setProfileGroup] = useState<Group | null>(null)
  const [showAddFriend, setShowAddFriend] = useState(false)
  const navigate = useNavigate()
  const { openDrawer } = useOutletContext<{ openDrawer: () => void }>()

  const sidebarRef = useRef<HTMLDivElement>(null)
  const { sidebarWidth, handleResizeStart } = useResizableSidebar('chat_sidebar_width', sidebarRef)

  // 加载群聊列表
  useEffect(() => {
    api.get('/groups').then(setGroups).catch(console.error)
  }, [])

  // 监听群设置面板发出的邀请事件
  useEffect(() => {
    const handler = () => setShowInvite(true)
    window.addEventListener('open-invite-modal', handler)
    return () => window.removeEventListener('open-invite-modal', handler)
  }, [])

  // 监听在线状态变化 → 更新群列表中的 online_count
  useEffect(() => {
    const handler = (e: Event) => {
      const msg = (e as CustomEvent).detail
      if (!msg?.data?.group_id) return
      setGroups((prev) => prev.map((g) => {
        if (g.id !== msg.data.group_id) return g
        const delta = msg.type === 'user_online' ? 1 : -1
        return { ...g, online_count: Math.max(0, (g.online_count || 0) + delta) }
      }))
    }
    window.addEventListener('online-count-change', handler)
    return () => window.removeEventListener('online-count-change', handler)
  }, [])

  // 群聊选中变化时刷新未读
  useEffect(() => {
    if (groupId) {
      api.post(`/groups/${groupId}/read`).catch(() => {})
    }
  }, [groupId])

  const currentGroup = groups.find((g) => g.id === groupId)

  const hasActiveConversation = !!(groupId || dmSessionId)

  // 移动端侧边栏全屏状态：初始化时根据当前条件直接计算，避免闪烁
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(() => {
    return !hasActiveConversation && window.innerWidth < 768
  })

  // 活跃对话状态变化时，自动切换移动端侧边栏
  useEffect(() => {
    if (window.innerWidth >= 768) return
    if (hasActiveConversation) {
      setMobileSidebarOpen(false)  // 进入对话 → 关闭侧边栏
    } else {
      setMobileSidebarOpen(true)   // 离开对话 → 全屏展示侧边栏
    }
  }, [hasActiveConversation])

  return (
    <div className="flex h-full relative">
      {/* 统一侧边栏：群聊 + 私信列表（桌面端可拖拽调整宽度；嵌入模式隐藏，由宿主导航） */}
      {!EMBED && (
      <div
        ref={sidebarRef}
        className={`shrink-0 ${mobileSidebarOpen ? 'absolute inset-0 z-overlay' : 'hidden md:block'} md:relative md:z-auto`}
        style={!mobileSidebarOpen ? { width: sidebarWidth } : undefined}
      >
        <ChatSidebar
          activeGroupId={groupId}
          activeSessionId={dmSessionId}
          onCreateGroup={() => setShowCreateGroup(true)}
          onAddFriend={() => setShowAddFriend(true)}
          openDrawer={openDrawer}
          hideOnMobile={hasActiveConversation && !mobileSidebarOpen}
          onMobileBack={mobileSidebarOpen ? () => setMobileSidebarOpen(false) : undefined}
          mobileFullscreen={mobileSidebarOpen}
        />
        {/* 拖拽手柄（仅桌面端） */}
        <div
          className="hidden md:block absolute top-0 -right-1.5 w-1.5 h-full cursor-col-resize hover:bg-primary-400/30 active:bg-primary-400/50 transition-colors z-overlay"
          onMouseDown={handleResizeStart}
        />
      </div>
      )}

      {/* ── 右侧主区域 ── */}
      {!hasActiveConversation ? (
        <div
          className="flex flex-1 items-center justify-center bg-canvas relative"
          onClick={mobileSidebarOpen ? () => setMobileSidebarOpen(false) : undefined}
        >
          <div className="text-center px-4">
            <MessageBubblePlaceholder />
            <p className="mt-4 text-base md:text-lg text-textSecondary font-medium">{t('chat.selectConversation')}</p>
            {groups.length === 0 ? (
              <button
                onClick={(e) => { e.stopPropagation(); setShowCreateGroup(true) }}
                className="mt-5 px-5 py-2.5 bg-primary-500 text-white rounded-card hover:bg-primary-600 text-sm font-medium transition-all shadow-lg shadow-primary-500/20"
              >
                {t('chat.createFirstGroup')}
              </button>
            ) : (
              <p className="mt-2 text-xs text-textMuted">{t('chat.selectFromListHint')}</p>
            )}
          </div>
        </div>
      ) : groupId ? (
        /* ── 群聊 ── */
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <div className="px-4 h-14 border-b border-border bg-surface flex items-center gap-2 shrink-0">
            <button
              onClick={() => navigate('/chat')}
              className="md:hidden p-1.5 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors"
              title={t('chat.sessionList')}
            >
              <ArrowLeft size={20} />
            </button>
            {currentGroup && (
              <GroupAvatarHeader g={currentGroup} onClick={() => setProfileGroup(currentGroup)} />
            )}
            <h2 className="font-semibold text-textPrimary text-sm truncate">
              # {currentGroup?.name || t('chat.loading')}
            </h2>
            {currentGroup?.is_federated && (
              <span className="chip chip-primary shrink-0"
                    title={t('chat.federatedGroup')}>
                <Globe size={11} />
                {t('chat.federated')}
              </span>
            )}
            <button
              onClick={() => setShowInvite(true)}
              className="p-1 rounded-control hover:bg-elevated text-textMuted hover:text-primary-400 transition-colors"
              title={t('chat.inviteMembers')}
            >
              <UserPlus size={16} />
            </button>
            <span className={`inline-flex items-center gap-1 text-3xs font-medium ${(currentGroup?.online_count ?? 0) === 0 ? 'text-slate-400' : 'text-mint-400'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${(currentGroup?.online_count ?? 0) === 0 ? 'bg-slate-400' : 'bg-mint-400'}`} /> {t('chat.onlineCount')}: {currentGroup?.online_count ?? 0}
            </span>
            <button
              onClick={async () => {
                if (!currentGroup) return
                try {
                  if (currentGroup.dnd_until) {
                    await api.post(`/groups/${currentGroup.id}/dnd/cancel`)
                    setGroups(prev => prev.map(g => g.id === currentGroup.id ? { ...g, dnd_until: null } : g))
                  } else {
                    await api.post(`/groups/${currentGroup.id}/dnd`, { group_id: currentGroup.id, duration_minutes: null })
                    setGroups(prev => prev.map(g => g.id === currentGroup.id ? { ...g, dnd_until: 'permanent' } : g))
                  }
                } catch { /* ignore */ }
              }}
              className={`p-1 rounded-control transition-colors ml-auto ${
                currentGroup?.dnd_until
                  ? 'text-rose-400 hover:bg-rose-400/10'
                  : 'text-textMuted hover:text-rose-400 hover:bg-elevated'
              }`}
              title={currentGroup?.dnd_until ? t('chat.unmute') : t('chat.mute')}
            >
              {currentGroup?.dnd_until ? <BellOff size={14} /> : <Bell size={14} />}
            </button>
            {currentGroup && (
              <button
                onClick={() => setShowSettings(true)}
                className="p-1 rounded-control hover:bg-elevated text-textMuted hover:text-textSecondary transition-colors"
                title={t('chat.groupSettings')}
              >
                <Settings size={14} />
              </button>
            )}
          </div>
          <ChatView conversationType="group" conversationId={groupId} myRole={currentGroup?.my_role} />
        </div>
      ) : (
        /* ── 私信 ── */
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <DMChatView sessionId={dmSessionId!} onMobileBack={() => setMobileSidebarOpen(true)} />
        </div>
      )}

      {/* 添加好友弹窗 */}
      {showAddFriend && (
        <Dialog onClose={() =>  setShowAddFriend(false)} className="flex items-center justify-center">
          <div
            className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl shadow-black/30 pb-[var(--safe-bottom)] md:pb-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-textPrimary">{t('list.add')}</h2>
              <button onClick={() => setShowAddFriend(false)} className="p-1 hover:bg-canvas rounded-control text-textMuted hover:text-textSecondary">
                <X size={18} />
              </button>
            </div>
            <SearchOverlay />
          </div>
        </Dialog>
      )}

      {profileGroup && (
        <ProfileCard
          entityType="group"
          entityId={profileGroup.id}
          entityName={profileGroup.name}
          avatar_url={profileGroup.avatar_url}
          onClose={() => setProfileGroup(null)}
        />
      )}

      {/* 创建群聊弹窗 */}
      {showCreateGroup && (
      <CreateGroupModal
          onClose={() => setShowCreateGroup(false)}
          onCreated={(newGroup) => {
            setShowCreateGroup(false)
            setGroups((prev) => [...prev, newGroup])
            navigate(`/chat/gm/${newGroup.id}`)
          }}
        />
      )}

      {/* 邀请成员弹窗 */}
      {showInvite && (
        <InviteMemberModal
          groupId={groupId!}
          onClose={() => setShowInvite(false)}
        />
      )}

      {/* 群设置面板 */}
      {showSettings && currentGroup && (
        <GroupSettingsPanel
          group={{
            id: currentGroup.id,
            name: currentGroup.name,
            owner_type: currentGroup.owner_type,
            owner_id: currentGroup.owner_id,
            is_vector_accelerated: currentGroup.is_vector_accelerated,
            is_paused: currentGroup.is_paused,
            is_pinned: currentGroup.is_pinned ?? false,
            concurrent_ai_limit: currentGroup.concurrent_ai_limit,
            announcement: currentGroup.announcement,
            speak_limit_per_minute: currentGroup.speak_limit_per_minute,
            speak_limit_window_seconds: currentGroup.speak_limit_window_seconds,
            my_role: currentGroup.my_role,
            avatar_mode: currentGroup.avatar_mode || 'default',
            avatar_url: currentGroup.avatar_url,
            include_ai_in_avatar: currentGroup.include_ai_in_avatar ?? true,
            // 发现与入群三开关：设置面板回显要用，别在这里漏字段（漏了开关永远是默认值）
            searchable: currentGroup.searchable ?? false,
            auto_approve_join: currentGroup.auto_approve_join ?? true,
            approve_invites: currentGroup.approve_invites ?? false,
          }}
          onClose={() => setShowSettings(false)}
          onUpdate={(updated) => {
            setGroups((prev) =>
              prev.map((g) => (g.id === currentGroup.id ? { ...g, ...updated } : g))
            )
          }}
          onLeave={() => {
            setShowSettings(false)
            setGroups((prev) => prev.filter((g) => g.id !== currentGroup.id))
            navigate('/chat')
          }}
        />
      )}
    </div>
  )
}

function MessageBubblePlaceholder() {
  return (
    <svg className="mx-auto" width="80" height="80" viewBox="0 0 80 80" fill="none">
      <rect x="10" y="15" width="60" height="45" rx="12" className="fill-elevated" />
      <circle cx="30" cy="37" r="8" className="fill-border" />
      <circle cx="50" cy="37" r="8" className="fill-border" />
    </svg>
  )
}

function CreateGroupModal({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (group: Group) => void
}) {
  const t = useT()
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // 成员搜索
  interface SearchResult { id: number; type: 'human' | 'ai'; name: string; state?: string }
  const [memberQuery, setMemberQuery] = useState('')
  const [memberResults, setMemberResults] = useState<SearchResult[]>([])
  const [memberSearchLoading, setMemberSearchLoading] = useState(false)
  const [selectedMembers, setSelectedMembers] = useState<Map<string, SearchResult>>(new Map())

  useEffect(() => {
    if (memberQuery.length < 1) { setMemberResults([]); return }
    const timer = setTimeout(async () => {
      setMemberSearchLoading(true)
      try {
        const data = await api.get<{ results: SearchResult[] }>(`/search?q=${encodeURIComponent(memberQuery)}`)
        setMemberResults(data.results)
      } catch { /* ignore */ }
      finally { setMemberSearchLoading(false) }
    }, 300)
    return () => clearTimeout(timer)
  }, [memberQuery])

  const toggleMember = (r: SearchResult) => {
    const key = `${r.type}:${r.id}`
    setSelectedMembers(prev => {
      const next = new Map(prev)
      if (next.has(key)) next.delete(key)
      else next.set(key, r)
      return next
    })
  }
  const removeMember = (key: string) => {
    setSelectedMembers(prev => { const next = new Map(prev); next.delete(key); return next })
  }

  const canCreate = name.trim() && selectedMembers.size > 0 && !loading

  const handleCreate = async () => {
    if (!canCreate) return
    setLoading(true)
    setError('')
    try {
      const initialMembers = Array.from(selectedMembers.values()).map(m => ({ type: m.type, id: m.id }))
      const newGroup = await api.post('/groups', { name: name.trim(), initial_members: initialMembers })
      onCreated(newGroup)
    } catch (err: any) {
      setError(err.message || t('chat.createFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog onClose={onClose} className="flex items-center justify-center">
      <div
        className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl shadow-black/30"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-4 text-textPrimary">{t('chat.createNewGroup')}</h2>
        <div className="mb-3">
          <label className="block text-xs font-medium mb-1.5 text-textSecondary">{t('chat.groupName')}</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            className="w-full px-3.5 py-2.5 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted text-sm focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            placeholder={t('chat.groupNamePlaceholder')}
            autoFocus
          />
        </div>

        {/* 成员搜索 */}
        <div className="mb-2">
          <label className="block text-xs font-medium mb-1.5 text-textSecondary">
            {t('chat.addMembers')} <span className="text-rose-400">*</span>
          </label>
          <input
            type="text" value={memberQuery}
            onChange={(e) => setMemberQuery(e.target.value)}
            className="w-full px-3.5 py-2 rounded-card border border-border bg-canvas text-textPrimary placeholder:text-textMuted text-sm focus:outline-none focus:ring-2 focus:ring-primary-500/50"
            placeholder={t('chat.searchMembers')}
          />
          {memberResults.length > 0 && (
            <div className="mt-1 border border-border rounded-card bg-canvas max-h-40 overflow-y-auto">
              {memberResults.map(r => {
                const key = `${r.type}:${r.id}`
                const sel = selectedMembers.has(key)
                return (
                  <button key={key} onClick={() => toggleMember(r)}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-elevated transition-colors ${sel ? 'bg-primary-500/10' : ''}`}>
                    <span className={`w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 ${sel ? 'bg-primary-500 border-primary-500' : 'border-border'}`}>
                      {sel && <Check size={10} className="text-white" />}
                    </span>
                    <span className="text-textPrimary truncate flex-1">{r.name}</span>
                    <span className="text-3xs text-textMuted shrink-0">{r.type === 'ai' ? 'AI' : t('chat.human')}</span>
                  </button>
                )
              })}
            </div>
          )}
          {memberSearchLoading && <div className="mt-1 text-xs text-textMuted">{t('common.searching')}...</div>}
        </div>

        {/* 已选成员 */}
        {selectedMembers.size > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {Array.from(selectedMembers.entries()).map(([key, r]) => (
              <span key={key} className="inline-flex items-center gap-1 px-2 py-1 bg-primary-500/10 border border-primary-500/20 rounded-control text-xs text-textPrimary">
                {r.type === 'ai' ? <Bot size={10} className="text-mint-400" /> : <User size={10} className="text-primary-400" />}
                {r.name}
                <button onClick={() => removeMember(key)} className="ml-0.5 hover:text-rose-400 transition-colors"><X size={12} /></button>
              </span>
            ))}
          </div>
        )}

        {error && <div className="text-sm text-rose-400 mb-3">{error}</div>}
        <div className="flex gap-2 mt-4">
          <button
            onClick={onClose}
            className="btn btn-md btn-outline flex-1"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleCreate}
            disabled={!canCreate}
            className="btn btn-md btn-primary flex-1"
          >
            {loading ? t('chat.creating') : t('chat.create')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function InviteMemberModal({
  groupId,
  onClose,
}: {
  groupId: number
  onClose: () => void
}) {
  const t = useT()
  interface SearchResult {
    id: number
    type: 'human' | 'ai'
    name: string
    state?: string
  }

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  // 好友预加载（空输入时显示；滚动到底加载更多）
  const [friends, setFriends] = useState<SearchResult[]>([])
  const [friendsOffset, setFriendsOffset] = useState(0)
  const [friendsLoading, setFriendsLoading] = useState(false)
  const [friendsHasMore, setFriendsHasMore] = useState(true)
  const friendsLoadingRef = useRef(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [selectedEntries, setSelectedEntries] = useState<Map<string, SearchResult>>(new Map())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState<string[]>([])
  const [showManual, setShowManual] = useState(false)
  const [manualType, setManualType] = useState<'ai' | 'human'>('ai')
  const [manualId, setManualId] = useState('')

  const loadFriends = async (reset = false) => {
    if (friendsLoadingRef.current) return
    friendsLoadingRef.current = true
    setFriendsLoading(true)
    try {
      const offset = reset ? 0 : friendsOffset
      const data = await api.get<
        { id: number; friend_type: string; friend_id: number; friend_name: string; state?: string }[]
      >(`/friends?limit=30&offset=${offset}`)
      const items: SearchResult[] = (Array.isArray(data) ? data : []).map((f) => ({
        id: f.friend_id,
        type: (f.friend_type === 'ai' ? 'ai' : 'human') as 'human' | 'ai',
        name: f.friend_name, state: f.state,
      }))
      setFriends((prev) => (reset ? items : [...prev, ...items]))
      setFriendsOffset(offset + items.length)
      setFriendsHasMore(items.length >= 30)
    } catch { /* ignore */ } finally {
      friendsLoadingRef.current = false
      setFriendsLoading(false)
    }
  }
  // 空输入 → 预加载好友；清空搜索时重置
  useEffect(() => {
    if (query.length < 1) loadFriends(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  // 防抖搜索
  useEffect(() => {
    if (query.length < 1) {
      setResults([])
      return
    }
    const timer = setTimeout(async () => {
      setSearchLoading(true)
      try {
        const data = await api.get<{ results: SearchResult[] }>(
          `/search?q=${encodeURIComponent(query)}`
        )
        setResults(data.results)
      } catch {
        // 静默失败
      } finally {
        setSearchLoading(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [query])

  const toggleMember = (r: SearchResult) => {
    const key = `${r.type}:${r.id}`
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
        setSelectedEntries((prevEntries) => {
          const nextEntries = new Map(prevEntries)
          nextEntries.delete(key)
          return nextEntries
        })
      } else {
        next.add(key)
        setSelectedEntries((prevEntries) => new Map(prevEntries).set(key, r))
      }
      return next
    })
  }

  const handleInviteSelected = async () => {
    if (selected.size === 0) return
    setLoading(true)
    setError('')
    setSuccess([])
    const ok: string[] = []
    const failedNames: string[] = []
    for (const key of selected) {
      const [member_type, idStr] = key.split(':')
      const member_id = parseInt(idStr)
      try {
        const res = await api.post(`/groups/${groupId}/invite`, { member_type, member_id })
        const entry = selectedEntries.get(key)
        const name = entry?.name || `${member_type}:${idStr}`
        if (res.method === 'invitation') {
          ok.push(`${name} ${t('chat.invitationSent')}`)
        } else {
          ok.push(`${name} ${t('chat.joined')}`)
        }
      } catch (err: any) {
        const entry = selectedEntries.get(key)
        const name = entry?.name || `${member_type}:${idStr}`
        const msg = err.message || t('chat.inviteFailed')
        const reason = msg.includes('已在群聊') || msg.includes('already') ? t('chat.alreadyInGroup') : msg
        failedNames.push(`${name} ${reason}`)
      }
    }
    setSuccess(ok)
    if (ok.length === selected.size) {
      setTimeout(onClose, 1200)
    } else if (ok.length === 0) {
      setError(failedNames.join('；'))
    } else {
      setError(failedNames.join('；'))
    }
    setLoading(false)
  }

  const handleManualInvite = async () => {
    const id = parseInt(manualId)
    if (!id || id <= 0) {
      setError(t('chat.enterValidId'))
      return
    }
    setLoading(true)
    setError('')
    try {
      await api.post(`/groups/${groupId}/invite`, {
        member_type: manualType,
        member_id: id,
      })
      setSuccess([`${manualType}:${id}`])
      setTimeout(onClose, 1000)
    } catch (err: any) {
      const msg = err.message || t('chat.inviteFailed')
      // 后端返回"该成员已在群聊中" → 友好显示
      setError(msg.includes('已在群聊') || msg.includes('already') ? `${manualType === 'ai' ? 'AI' : '用户'} ID:${id} ${t('chat.alreadyInGroup')}` : msg)
    } finally {
      setLoading(false)
    }
  }

  // 在线状态：CSS 圆点（不用 emoji）
  const stateDot = (s?: string) => {
    switch (s) {
      case 'active': return <span className="inline-block w-2 h-2 rounded-full bg-mint-400 shrink-0" title="在线" />
      case 'dnd': return <span className="inline-block w-2 h-2 rounded-full bg-rose-400 shrink-0" title="忙碌" />
      case 'inactive': return <span className="inline-block w-2 h-2 rounded-full bg-slate-500 shrink-0" title="离线" />
      default: return null
    }
  }

  // 统一列表项渲染（好友列表 / 搜索结果共用）
  const renderEntry = (r: SearchResult) => {
    const key = `${r.type}:${r.id}`
    const checked = selected.has(key)
    return (
      <label
        key={key}
        className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-colors ${
          checked ? 'bg-primary-500/10' : 'hover:bg-elevated'
        }`}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={() => toggleMember(r)}
          className="w-4 h-4 rounded border-border bg-canvas text-primary-500 focus:ring-primary-500/50"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-textPrimary truncate">{r.name}</span>
            <span className="text-xs">{stateDot(r.state)}</span>
          </div>
        </div>
        <span className="text-xs text-textMuted shrink-0">
          {r.type === 'ai' ? <><Bot size={12} className="inline" /> AI</> : <User size={12} className="inline" />}
        </span>
      </label>
    )
  }

  return (
    <Dialog onClose={onClose} layer="toast" className="flex items-center justify-center">
      <div
        className="bg-elevated border border-border rounded-dialog p-6 w-full max-w-md mx-4 shadow-2xl shadow-black/30 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-1 text-textPrimary">{t('chat.inviteMembers')}</h2>
        <p className="text-xs text-textMuted mb-4">{t('chat.inviteSearchHint')}</p>

        {/* 搜索框 */}
        <div className="relative mb-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('chat.searchNamePlaceholder')}
            className="w-full px-3 py-2 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
          />
        </div>

        {/* 搜索结果 */}
        <div
          className="flex-1 overflow-y-auto border border-border rounded-card divide-y divide-border/50 mb-4 max-h-64"
          onScroll={(e) => {
            const el = e.currentTarget
            // 空输入（好友列表）滚到底 → 加载更多；搜索模式不加载
            if (query.length > 0) return
            if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) loadFriends()
          }}
        >
                    {query.length < 1 ? (
            friendsLoading && friends.length === 0 ? (
              <div className="flex items-center justify-center py-6">
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-primary-500" />
              </div>
            ) : friends.length === 0 ? (
              <div className="py-6 text-center text-xs text-textMuted">
                {t('chat.inviteSearchPrompt')}
              </div>
            ) : (
              friends.map(renderEntry)
            )
          ) : searchLoading ? (
            <div className="flex items-center justify-center py-6">
              <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-primary-500" />
            </div>
          ) : results.length === 0 ? (
            <div className="py-6 text-center text-xs text-textMuted">{t('common.noResults')}</div>
          ) : (
            results.map(renderEntry)
          )}
          {/* 底部加载更多指示（好友列表滚动加载） */}
          {query.length < 1 && friendsLoading && friends.length > 0 && (
            <div className="py-2.5 text-center text-xs text-textMuted">加载中…</div>
          )}
        </div>

        {/* 已选成员列表（独立区域，搜索不影响） */}
        {selected.size > 0 && (
          <div className="mb-3">
            <div className="text-xs text-textMuted mb-1.5">{t('chat.selectedMembers').replace('{size}', String(selected.size))}</div>
            <div className="flex flex-wrap gap-1.5">
              {Array.from(selectedEntries.entries()).map(([key, r]) => (
                <span key={key} className="inline-flex items-center gap-1 px-2 py-1 bg-primary-500/10 border border-primary-500/20 rounded-control text-xs text-textPrimary">
                  {r.type === 'ai' ? <Bot size={10} className="text-mint-400" /> : <User size={10} className="text-primary-400" />}
                  {r.name}
                  <button onClick={() => toggleMember(r)} className="ml-0.5 hover:text-rose-400 transition-colors" title="取消选择"><X size={12} /></button>
                </span>
              ))}
            </div>
          </div>
        )}

        {!showManual ? (
          <button
            onClick={() => setShowManual(true)}
            className="text-xs text-textMuted hover:text-primary-400 mb-3 self-start"
          >
            {t('chat.manualId')}
          </button>
        ) : (
          <div className="space-y-2 mb-3 border border-dashed border-border rounded-card p-3">
            <div className="flex gap-2">
              {(['ai', 'human'] as const).map((type) => (
                <button
                  key={type}
                  onClick={() => setManualType(type)}
                  className={`flex-1 py-1.5 text-xs rounded-control border transition-colors ${
                    manualType === type
                      ? 'bg-primary-500/15 border-primary-500/40 text-primary-600 dark:text-primary-300'
                      : 'border-border text-textSecondary hover:bg-elevated'
                  }`}
                >
                  {type === 'ai' ? <><Bot size={12} className="inline" /> AI</> : <><User size={12} className="inline" /> {t('list.human')}</>}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                type="number"
                value={manualId}
                onChange={(e) => setManualId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleManualInvite()}
                className="flex-1 px-3 py-1.5 rounded-control border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                placeholder={t('chat.manualIdPlaceholder')}
                min={1}
              />
              <button
                onClick={handleManualInvite}
                disabled={!manualId.trim() || loading}
                className="px-3 py-1.5 text-xs bg-elevated text-textSecondary rounded-control hover:bg-border disabled:opacity-30"
              >
                {t('chat.invite')}
              </button>
            </div>
            <button onClick={() => setShowManual(false)} className="text-xs text-textMuted hover:text-textSecondary">
              {t('common.collapse')}
            </button>
          </div>
        )}

        {error && (
          <div className="text-sm text-rose-400 bg-rose-500/10 border border-rose-500/30 rounded-control px-3 py-2.5 mb-3 flex items-start gap-2 whitespace-pre-line">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}
        {success.length > 0 && (
          <div className="text-sm text-mint-400 mb-2">{t('chat.inviteSuccess').replace('{success}', String(success.length))}</div>
        )}

        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="btn btn-md btn-outline flex-1"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleInviteSelected}
            disabled={selected.size === 0 || loading}
            className="btn btn-md btn-primary flex-1"
          >
            {loading ? t('chat.inviting') : t('chat.inviteCount').replace('{count}', String(selected.size))}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
