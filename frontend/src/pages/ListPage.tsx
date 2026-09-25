import { useState, useEffect, useMemo, type ReactNode } from 'react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useT } from '../i18n/I18nContext'
import { Users, MessageSquare, UserPlus, Check, X, Search, ArrowUpDown, ArrowLeft, Bot, User, Menu, Star } from 'lucide-react'
import {
  notifyRequestsChanged, fetchPendingRequests, REQUESTS_CHANGED, REQUESTS_POLL_MS,
  type PendingRequest,
} from '../hooks/usePendingRequests'
import { getStateDotColor } from '../constants'
import { getStatusTextStyle, BG_CANVAS_LIGHT, BG_CANVAS_DARK } from '../utils/statusColor.tsx'
import { useTheme } from '../context/ThemeContext'

interface Friend {
  id: number
  friend_type: string
  friend_id: number
  friend_name: string
  state: string | null
  avatar_url: string | null
  status_text: string | null
  status_color: string | null
  is_priority: boolean
  created_at: string | null
  last_dm_at: string | null
  friend_user_id?: number | null
}

interface FriendRequest {
  id: number
  requester_id: number
  requester_name: string | null
  requester_avatar_url: string | null
  target_type: string
  target_id: number
  target_name: string | null
  target_avatar_url: string | null
  status: string
  direction: string | null
  message: string | null
  auto_respond_friend_request?: boolean
  created_at: string | null
}

// 头像组件：优先显示真实头像，否则显示首字母
function AvatarPic({ url, name, size = 'md' }: { url: string | null | undefined; name: string; size?: 'sm' | 'md' | 'lg' }) {
  const sizeClass = size === 'sm' ? 'w-7 h-7 text-3xs' : size === 'lg' ? 'w-12 h-12 text-xl' : 'w-10 h-10 text-lg'
  return (
    <div className={`${sizeClass} rounded-full shrink-0 overflow-hidden relative ${!url ? 'bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center shadow shadow-primary-500/15' : ''}`}>
      {url ? (
        <>
          <div className="absolute inset-px rounded-full bg-gradient-to-br from-primary-500 to-primary-700/30" />
          <img src={url} alt={name} className="relative w-full h-full rounded-full object-cover" loading="lazy" />
        </>
      ) : (
        <span className="text-white font-bold">{name.charAt(0)}</span>
      )}
    </div>
  )
}

/** 申请分区标题：三种申请共用同一排版 */
function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="px-4 py-2 bg-surface sticky top-0 z-10 border-b border-border/50">
      <span className="text-xs font-semibold text-textSecondary uppercase tracking-wider">
        {label}{count}
      </span>
    </div>
  )
}

/**
 * 申请行：头像 + 名字/说明 + 通过/拒绝。
 * 好友申请、入群申请、成员邀请审批共用它——三处各写一份 markup 迟早长歪。
 * onAccept 省略时只渲染拒绝/撤回按钮（发出的申请只能撤回）。
 */
function RequestRow({
  name, avatarUrl, note, badge, onAccept, acceptTitle, onReject, rejectTitle,
}: {
  name: string
  avatarUrl?: string | null
  note: string
  badge?: ReactNode
  onAccept?: () => void
  acceptTitle?: string
  onReject: () => void
  rejectTitle: string
}) {
  return (
    <div className="px-4 py-3.5">
      <div className="flex items-center gap-3">
        <AvatarPic url={avatarUrl} name={name || '?'} />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-medium text-textPrimary truncate block">{name}</span>
          <span className="text-xs text-textMuted">{note}</span>
          {badge}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {onAccept && (
            <button
              onClick={onAccept}
              className="p-1.5 rounded-control bg-mint-400/15 text-mint-400 hover:bg-mint-400/25 transition-colors"
              title={acceptTitle}
            >
              <Check size={16} />
            </button>
          )}
          <button
            onClick={onReject}
            className="p-1.5 rounded-control bg-rose-400/15 text-rose-400 hover:bg-rose-400/25 transition-colors"
            title={rejectTitle}
          >
            <X size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}

type Tab = 'list' | 'requests'
type SortMode = 'smart' | 'alpha' | 'recent_chat' | 'added_time'

/** 群申请审批路径：入群申请与成员邀请只是路径不同，动作语义一致（通过=approve） */
const GROUP_APPROVAL_PATHS = {
  group_join: (id: number, approve: boolean) =>
    `/groups/join-requests/${id}/${approve ? 'approve' : 'reject'}`,
  group_invite: (id: number, approve: boolean) =>
    `/group-invitations/${id}/${approve ? 'approve' : 'deny'}`,
} as const

const SORT_OPTIONS: { value: SortMode; key: string }[] = [
  { value: 'smart', key: 'list.smartSort' },
  { value: 'alpha', key: 'list.sortAlpha' },
  { value: 'recent_chat', key: 'list.sortRecent' },
  { value: 'added_time', key: 'list.sortAdded' },
]

// 状态权重：在线 > 勿扰 > 离线
function stateWeight(s: string | null): number {
  switch (s) {
    case 'active': return 0
    case 'dnd': return 1
    default: return 2
  }
}

// 类型权重：人类 > AI
function typeWeight(t: string): number {
  return t === 'human' ? 0 : 1
}

function sortFriends(friends: Friend[], mode: SortMode): Friend[] {
  const list = [...friends]
  switch (mode) {
    case 'smart':
      // 在线在前 → 人类在前 → 字典序（中文首字母）
      list.sort((a, b) => {
        const s = stateWeight(a.state) - stateWeight(b.state)
        if (s !== 0) return s
        const t = typeWeight(a.friend_type) - typeWeight(b.friend_type)
        if (t !== 0) return t
        return a.friend_name.localeCompare(b.friend_name, 'zh-CN')
      })
      break
    case 'alpha':
      // 纯字典序
      list.sort((a, b) => a.friend_name.localeCompare(b.friend_name, 'zh-CN'))
      break
    case 'recent_chat':
      // 最近聊天时间越近越顶（无时间的排末尾）
      list.sort((a, b) => {
        const ta = a.last_dm_at ? new Date(a.last_dm_at).getTime() : 0
        const tb = b.last_dm_at ? new Date(b.last_dm_at).getTime() : 0
        return tb - ta
      })
      break
    case 'added_time':
      // 加好友时间越新越顶
      list.sort((a, b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0
        return tb - ta
      })
      break
  }
  return list
}

export default function ListPage() {
  const t = useT()
  const [searchParams] = useSearchParams()
  const [friends, setFriends] = useState<Friend[]>([])
  const [requests, setRequests] = useState<FriendRequest[]>([])
  const [groupRequests, setGroupRequests] = useState<PendingRequest[]>([])
  const [loading, setLoading] = useState(true)
  // ?tab=requests 深链：群设置面板的「待审批申请」跳到这里
  const [tab, setTab] = useState<Tab>(searchParams.get('tab') === 'requests' ? 'requests' : 'list')
  const [sortMode, setSortMode] = useState<SortMode>('smart')
  const [searchQuery, setSearchQuery] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const { user } = useAuth()
  const { theme } = useTheme()
  const navigate = useNavigate()
  const { openDrawer } = useOutletContext<{ openDrawer: () => void }>()

  useEffect(() => {
    loadAll()
  }, [])

  // 申请列表的实时性：别人发来的靠轮询，自己处理完靠广播（红点同一个事件）
  useEffect(() => {
    if (tab !== 'requests') return
    const reload = () => loadGroupRequests()
    const iv = setInterval(reload, REQUESTS_POLL_MS)
    window.addEventListener(REQUESTS_CHANGED, reload)
    return () => {
      clearInterval(iv)
      window.removeEventListener(REQUESTS_CHANGED, reload)
    }
  }, [tab])

  /** 只拉群相关的申请：好友申请走 /friends/requests（那边才有 auto_respond 等字段） */
  const loadGroupRequests = async () => {
    try {
      const items = await fetchPendingRequests()
      setGroupRequests(items.filter(r => r.kind !== 'friend'))
    } catch (err) {
      console.error('加载申请列表失败:', err)
    }
  }

  const loadAll = async () => {
    setLoading(true)
    try {
      const [friendsRes, reqRes] = await Promise.all([
        api.get<Friend[]>('/friends'),
        api.get<FriendRequest[]>('/friends/requests'),
      ])
      setFriends(friendsRes)
      setRequests(reqRes.filter(r => r.status === 'pending'))
      await loadGroupRequests()
    } catch (err) {
      console.error('加载列表数据失败:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleStartDM = async (friendType: string, friendId: number, friendUserId?: number | null) => {
    try {
      const targetUserId = friendUserId || friendId
      const dm = await api.post(`/dm/${targetUserId}`)
      if (dm.session_id) {
        navigate(`/chat/dm/${dm.session_id}`)
      }
    } catch (err: any) {
      console.error('创建私信失败:', err)
    }
  }

  const handleAccept = async (requestId: number) => {
    const req = requests.find(r => r.id === requestId)
    if (req && req.direction === 'received' && req.auto_respond_friend_request) {
      if (!confirm(t('list.autoRespondConfirm'))) {
        return
      }
    }
    try {
      await api.post(`/friends/requests/${requestId}/accept`)
      loadAll()
      notifyRequestsChanged()   // 红点即时消失，不等 30s 轮询
    } catch (err: any) {
      console.error('接受好友申请失败:', err)
    }
  }

  const handleReject = async (requestId: number) => {
    try {
      await api.post(`/friends/requests/${requestId}/reject`)
      loadAll()
      notifyRequestsChanged()
    } catch (err: any) {
      console.error('拒绝好友申请失败:', err)
    }
  }

  const handleCancelSent = async (requestId: number) => {
    try {
      await api.post(`/friends/requests/${requestId}/reject`)
      loadAll()
      notifyRequestsChanged()
    } catch (err: any) {
      console.error('撤回好友申请失败:', err)
    }
  }

  /** 群申请审批（入群申请 / 成员邀请）。仅群主/管理员能看到条目，后端已按审批权过滤 */
  const handleGroupApproval = async (
    kind: 'group_join' | 'group_invite', requestId: number, approve: boolean,
  ) => {
    try {
      await api.post(GROUP_APPROVAL_PATHS[kind](requestId, approve))
      await loadGroupRequests()
      notifyRequestsChanged()
    } catch (err: any) {
      console.error('处理群申请失败:', err)
      alert(err.message || t('list.actionFailed'))
    }
  }

  // 拆分收到和发出的申请
  const receivedRequests = requests.filter(r => r.direction === 'received')
  const sentRequests = requests.filter(r => r.direction === 'sent')
  const joinRequests = groupRequests.filter(r => r.kind === 'group_join')
  const inviteRequests = groupRequests.filter(r => r.kind === 'group_invite')
  // 红点口径与后端 /requests/pending 一致：三类待处理申请都算
  const pendingCount = receivedRequests.length + joinRequests.length + inviteRequests.length
  const hasAnyRequest = pendingCount > 0 || sentRequests.length > 0

  const stateIcon = (s: string | null) => (
    <span className={`w-2 h-2 rounded-full shrink-0 ${getStateDotColor(s)}`} />
  )

  // 排序 + 搜索过滤
  const sortedFriends = useMemo(() => {
    const sorted = sortFriends(friends, sortMode)
    if (!searchQuery.trim()) return sorted
    const q = searchQuery.trim().toLowerCase()
    return sorted.filter(f => f.friend_name.toLowerCase().includes(q))
  }, [friends, sortMode, searchQuery])

  // 搜索框组件（复用，缓存避免无关状态变化时重建）
  const searchBox = useMemo(() => (
    <div className="relative">
      <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-textMuted" />
      <input
        type="text"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        placeholder={t('list.searchPlaceholder')}
        className="w-full pl-9 pr-8 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
        autoFocus={showSearch}
      />
      {searchQuery && (
        <button
          onClick={() => setSearchQuery('')}
          className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded text-textMuted hover:text-textSecondary"
        >
          <X size={14} />
        </button>
      )}
    </div>
  ), [searchQuery, showSearch])

  return (
    <div className="h-full flex flex-col bg-canvas">
      {/* 头部 */}
      <div className="px-4 h-14 border-b border-border bg-surface flex items-center gap-2 shrink-0">
        <button
          onClick={openDrawer}
          className="icon-btn-sm md:hidden -ml-1 text-textSecondary"
          title={t('chatlist.menu')}
        >
          <Menu size={18} />
        </button>
        <h1 className="font-semibold text-textPrimary text-sm flex items-center gap-2">
          <Users size={16} className="text-primary-400 hidden md:inline" />
          {tab === 'list' ? t('list.tabFriends') : t('list.tabRequests')}
        </h1>

        {/* 排序 + 搜索按钮（仅「列表」Tab） */}
        {tab === 'list' && friends.length > 0 && (
          <div className="ml-auto flex items-center gap-1">
            {/* 排序下拉 */}
            <div className="relative">
              <select
                value={sortMode}
                onChange={(e) => setSortMode(e.target.value as SortMode)}
                className="appearance-none pl-2 pr-6 py-1 rounded-control border border-border bg-canvas text-2xs text-textSecondary focus:outline-none focus:ring-1 focus:ring-primary-500/50 cursor-pointer"
              >
                {SORT_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{t(o.key)}</option>
                ))}
              </select>
              <ArrowUpDown size={10} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-textMuted pointer-events-none" />
            </div>
            {/* 搜索按钮 */}
            <button
              onClick={() => setShowSearch(true)}
              className="p-1.5 rounded-control hover:bg-elevated text-textMuted hover:text-textSecondary transition-colors"
              title={t('list.searchButton')}
            >
              <Search size={16} />
            </button>
          </div>
        )}
      </div>

      {/* Tab 切换 */}
      <div className="flex border-b border-border bg-surface shrink-0">
        <button
          onClick={() => setTab('list')}
          className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
            tab === 'list'
              ? 'text-primary-400 border-b-2 border-primary-400'
              : 'text-textMuted hover:text-textSecondary'
          }`}
        >
          {t('list.tabFriends')}
        </button>
        <button
          onClick={() => setTab('requests')}
          className={`flex-1 py-2.5 text-xs font-medium transition-colors relative ${
            tab === 'requests'
              ? 'text-primary-400 border-b-2 border-primary-400'
              : 'text-textMuted hover:text-textSecondary'
          }`}
        >
          {t('list.tabRequests')}
          {pendingCount > 0 && (
            <span className="absolute top-1 right-4 inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 text-3xs font-bold text-white bg-rose-500 rounded-full">
              {pendingCount}
            </span>
          )}
        </button>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto pb-[var(--safe-bottom)] md:pb-0">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-500" />
          </div>
        ) : tab === 'list' ? (
          /* 列表（好友） */
          friends.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-textMuted">
              <Users size={40} className="mb-3 opacity-30" />
              <p className="text-sm">{t('list.noFriends')}</p>
              <p className="text-xs mt-1">{t('list.noFriendsHint')}</p>
            </div>
          ) : sortedFriends.length === 0 && searchQuery ? (
            <div className="flex flex-col items-center justify-center py-20 text-textMuted">
              <Search size={40} className="mb-3 opacity-30" />
              <p className="text-sm">{t('list.noSearchResults')}</p>
              <p className="text-xs mt-1">{t('list.tryOtherKeywords')}</p>
            </div>
          ) : (
            <div className="divide-y divide-border/50">
              {sortedFriends.map((f) => (
                <button
                  key={`${f.friend_type}:${f.friend_id}`}
                  onClick={() => handleStartDM(f.friend_type, f.friend_id, f.friend_user_id)}
                  className="w-full flex items-center gap-3 px-4 py-3.5 hover:bg-elevated transition-colors text-left"
                >
                  <AvatarPic url={f.avatar_url} name={f.friend_name} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-textPrimary truncate">
                        {f.friend_name}
                      </span>
                      {f.status_text && (
                        <span className="text-xs font-medium truncate" style={f.status_color
                          ? getStatusTextStyle(f.status_color, theme === 'dark' ? BG_CANVAS_DARK : BG_CANVAS_LIGHT)
                          : undefined}>
                          · {f.status_text}
                        </span>
                      )}
                      {f.is_priority && <Star size={10} className="text-accent-400 fill-accent-400 shrink-0" />}
                      {stateIcon(f.state)}
                    </div>
                    <span className="text-xs text-textMuted">
                      {f.friend_type === 'ai' ? <><Bot size={12} className="inline" /> {t('list.friendAi')}</> : <><User size={12} className="inline" /> {t('list.friendHuman')}</>}
                    </span>
                  </div>
                  <MessageSquare size={16} className="text-textMuted shrink-0" />
                </button>
              ))}
            </div>
          )
        ) : (
          /* 申请列表：好友申请 / 入群申请 / 成员邀请审批 / 我发出的申请 */
          !hasAnyRequest ? (
            <div className="flex flex-col items-center justify-center py-20 text-textMuted">
              <UserPlus size={40} className="mb-3 opacity-30" />
              <p className="text-sm">{t('list.noPendingRequests')}</p>
              <p className="text-xs mt-1">{t('list.pendingHint')}</p>
            </div>
          ) : (
            <div className="divide-y divide-border/50">
              {receivedRequests.length > 0 && (
                <>
                  <SectionHeader label={t('list.receivedRequests')} count={receivedRequests.length} />
                  {receivedRequests.map((req) => (
                    <RequestRow
                      key={`recv-${req.id}`}
                      name={req.requester_name || `${t('list.userPrefix')}${req.requester_id}`}
                      avatarUrl={req.requester_avatar_url}
                      note={req.message || t('list.defaultRequestMessage')}
                      badge={req.auto_respond_friend_request ? (
                        <span className="inline-block mt-0.5 text-3xs text-accent-400 bg-accent-400/10 px-1.5 py-0.5 rounded">
                          {t('list.autoRespondWarning')}
                        </span>
                      ) : undefined}
                      onAccept={() => handleAccept(req.id)}
                      acceptTitle={t('list.accept')}
                      onReject={() => handleReject(req.id)}
                      rejectTitle={t('list.reject')}
                    />
                  ))}
                </>
              )}

              {joinRequests.length > 0 && (
                <>
                  <SectionHeader label={t('list.joinRequests')} count={joinRequests.length} />
                  {joinRequests.map((req) => (
                    <RequestRow
                      key={`join-${req.id}`}
                      name={req.user_name || `${t('list.userPrefix')}${req.user_id}`}
                      avatarUrl={req.avatar_url}
                      note={`${req.message ? `${req.message} · ` : ''}${t('list.applyToJoin')}「${req.group_name || ''}」`}
                      onAccept={() => handleGroupApproval('group_join', req.id, true)}
                      acceptTitle={t('list.accept')}
                      onReject={() => handleGroupApproval('group_join', req.id, false)}
                      rejectTitle={t('list.reject')}
                    />
                  ))}
                </>
              )}

              {inviteRequests.length > 0 && (
                <>
                  <SectionHeader label={t('list.inviteApprovals')} count={inviteRequests.length} />
                  {inviteRequests.map((req) => (
                    <RequestRow
                      key={`invite-${req.id}`}
                      name={req.target_name || `${t('list.userPrefix')}${req.target_id}`}
                      avatarUrl={req.avatar_url}
                      note={`${req.user_name || ''} ${t('list.invitedToJoin')}「${req.group_name || ''}」`}
                      onAccept={() => handleGroupApproval('group_invite', req.id, true)}
                      acceptTitle={t('list.approve')}
                      onReject={() => handleGroupApproval('group_invite', req.id, false)}
                      rejectTitle={t('list.deny')}
                    />
                  ))}
                </>
              )}

              {sentRequests.length > 0 && (
                <>
                  <SectionHeader label={t('list.sentRequests')} count={sentRequests.length} />
                  {sentRequests.map((req) => (
                    <RequestRow
                      key={`sent-${req.id}`}
                      name={req.target_name || `${t('list.userPrefix')}${req.target_id}`}
                      avatarUrl={req.target_avatar_url}
                      note={req.message || t('list.sentRequestMessage')}
                      onReject={() => handleCancelSent(req.id)}
                      rejectTitle={t('list.cancelRequest')}
                    />
                  ))}
                </>
              )}
            </div>
          )
        )}
      </div>

      {/* ── 搜索弹窗 ── */}
      {showSearch && (
        <>
          {/* 移动端：全屏 */}
          <div className="md:hidden fixed inset-0 z-modal bg-surface flex flex-col">
            <div className="px-4 h-14 border-b border-border flex items-center gap-3 shrink-0">
              <button
                onClick={() => { setShowSearch(false); setSearchQuery('') }}
                className="p-1.5 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors"
              >
                <ArrowLeft size={20} />
              </button>
              <div className="flex-1">
                {searchBox}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto divide-y divide-border/50 pb-[var(--safe-bottom)]">
              {sortedFriends.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-20 text-textMuted">
                  <Search size={40} className="mb-3 opacity-30" />
                  <p className="text-sm">{t('list.noSearchResults')}</p>
                </div>
              ) : (
                sortedFriends.map((f) => (
                  <button
                    key={`${f.friend_type}:${f.friend_id}`}
                    onClick={() => {
                      setShowSearch(false)
                      setSearchQuery('')
                      handleStartDM(f.friend_type, f.friend_id, f.friend_user_id)
                    }}
                    className="w-full flex items-center gap-3 px-4 py-3.5 hover:bg-elevated transition-colors text-left"
                  >
                    <AvatarPic url={f.avatar_url} name={f.friend_name} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-textPrimary truncate">{f.friend_name}</span>
                        {stateIcon(f.state)}
                      </div>
                      <span className="text-xs text-textMuted">
                        {f.friend_type === 'ai' ? <><Bot size={12} className="inline" /> {t('list.friendAi')}</> : <><User size={12} className="inline" /> {t('list.friendHuman')}</>}
                      </span>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>

          {/* 桌面端：悬浮下拉 */}
          <div className="hidden md:block fixed inset-0 z-modal" onClick={() => { setShowSearch(false); setSearchQuery('') }}>
            <div
              className="absolute top-[4.5rem] left-1/2 -translate-x-1/2 w-full max-w-sm"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="bg-elevated border border-border rounded-dialog shadow-2xl shadow-black/30 mx-4 overflow-hidden">
                <div className="p-3">
                  {searchBox}
                </div>
                {searchQuery.trim() && (
                  <div className="max-h-64 overflow-y-auto border-t border-border divide-y divide-border/50">
                    {sortedFriends.length === 0 ? (
                      <div className="py-6 text-center text-xs text-textMuted">{t('list.searchEmptyDesktop')}</div>
                    ) : (
                      sortedFriends.slice(0, 8).map((f) => (
                        <button
                          key={`${f.friend_type}:${f.friend_id}`}
                          onClick={() => {
                            setShowSearch(false)
                            setSearchQuery('')
                            handleStartDM(f.friend_type, f.friend_id, f.friend_user_id)
                          }}
                          className="w-full flex items-center gap-3 px-4 py-3 hover:bg-canvas transition-colors text-left"
                        >
                          <AvatarPic url={f.avatar_url} name={f.friend_name} size="sm" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-textPrimary truncate">{f.friend_name}</span>
                              {stateIcon(f.state)}
                            </div>
                            <span className="text-3xs text-textMuted">
                              {f.friend_type === 'ai' ? <><Bot size={12} className="inline" /> {t('list.friendAi')}</> : <><User size={12} className="inline" /> {t('list.friendHuman')}</>}
                            </span>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
