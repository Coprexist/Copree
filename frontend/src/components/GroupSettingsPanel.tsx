import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { fetchPendingRequests } from '../hooks/usePendingRequests'
import { useT } from '../i18n/I18nContext'
import { getStateDotColor } from '../constants'
import { X, Bell, Pause, BellOff, LogOut, UserX, Shield, ShieldOff, UserPlus, Volume2, VolumeX, Download, Clock, Globe, Loader2, ArrowLeft, Crown, Pin, PinOff, Image, Camera, Users, CheckCircle2 } from 'lucide-react'
import Toggle from './Toggle'
import AvatarPickerModal from './AvatarPickerModal'

// ── 联邦共享状态（v0.2.0: 群主/AI制作者按群控制联邦共享） ──

interface FederationPeerStatus {
  peer_id: number
  display_name: string
  peer_public_id: string
  is_connected: boolean
  is_shared: boolean
  federated_id: string
}

function FederationShareSection({ groupId }: { groupId: number }) {
  const t = useT()
  const [peers, setPeers] = useState<FederationPeerStatus[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [toggling, setToggling] = useState<number | null>(null)
  const [myDisplayName, setMyDisplayName] = useState('')

  const loadPeers = async () => {
    setLoading(true)
    try {
      const data = await api.get<{
        success: boolean
        my_display_name: string
        peers: FederationPeerStatus[]
      }>(`/groups/${groupId}/federation/peers`)
      setPeers(data.peers || [])
      setMyDisplayName(data.my_display_name || '')
    } catch {
      // 静默失败
    } finally {
      setLoading(false)
      setLoaded(true)
    }
  }

  // 首次自动加载
  useEffect(() => {
    loadPeers()
  }, [groupId])

  const handleToggle = async (peer: FederationPeerStatus) => {
    setToggling(peer.peer_id)
    try {
      if (peer.is_shared) {
        await api.post(`/groups/${groupId}/federation/unshare`, { peer_ids: [peer.peer_id] })
      } else {
        await api.post(`/groups/${groupId}/federation/share`, { peer_ids: [peer.peer_id] })
      }
      await loadPeers()
    } catch (e: any) {
      const msg = e?.response?.data?.detail || (peer.is_shared ? '取消共享失败' : '共享失败')
      alert(msg)
    } finally {
      setToggling(null)
    }
  }

  const sharedCount = peers.filter(p => p.is_shared).length

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-textPrimary font-medium flex items-center gap-1">
            <Globe size={14} className="text-textMuted shrink-0" />
            {t('groupSettings.federationShare')}
          </div>
          <div className="text-xs text-textMuted">
            {sharedCount > 0
              ? `${t('groupSettings.sharedTo')}${sharedCount}${t('groupSettings.autoForward')}`
              : t('groupSettings.federationShareHint')}
          </div>
        </div>
        <button
          onClick={loadPeers}
          disabled={loading}
          className="text-xs text-textMuted hover:text-textSecondary transition-colors"
        >
          {loading ? <Loader2 size={14} className="animate-spin" /> : t('common.refresh')}
        </button>
      </div>

      {!myDisplayName && loaded && (
        <div className="bg-accent-400/10 text-accent-400 rounded-control px-3 py-2 text-xs">
          {t('groupSettings.federationNoDisplayName')}
        </div>
      )}

      {loaded && peers.length === 0 && (
        <div className="bg-elevated rounded-control px-3 py-3 text-xs text-textMuted text-center">
          <p className="font-medium text-textSecondary mb-1">{t('groupSettings.federationNoPeers')}</p>
          <p>{t('groupSettings.federationNoPeersHint')}</p>
        </div>
      )}

      {peers.length > 0 && (
        <div className="space-y-1">
          {peers.map(peer => (
            <div
              key={peer.peer_id}
              className="flex items-center justify-between px-3 py-2 rounded-control bg-elevated hover:bg-canvas transition-colors"
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${
                    peer.is_connected ? 'bg-mint-400' : 'bg-textMuted/40'
                  }`}
                />
                <div className="min-w-0">
                  <div className="text-sm text-textPrimary truncate">{peer.display_name}</div>
                  <div className="text-3xs text-textMuted">
                    {peer.is_connected
                      ? t('groupSettings.federationPeerConnected')
                      : t('groupSettings.federationPeerDisconnected')}
                  </div>
                </div>
              </div>
              {toggling === peer.peer_id ? (
                <div className="w-12 h-6 flex items-center justify-center">
                  <Loader2 size={16} className="animate-spin text-textMuted" />
                </div>
              ) : (
                <Toggle checked={peer.is_shared} onChange={() => handleToggle(peer)} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface GroupMember {
  type: string
  id: number
  name: string
  state?: string
  role: string
  dnd_until?: string | null
}

interface GroupSettings {
  id: number
  name: string
  owner_type: string
  owner_id: number
  is_vector_accelerated: boolean
  is_paused: boolean
  announcement: string | null
  speak_limit_per_minute: number
  speak_limit_window_seconds: number
  concurrent_ai_limit: number
  my_role: string
  is_pinned?: boolean
  avatar_mode?: string
  avatar_url?: string | null
  include_ai_in_avatar?: boolean
  // 发现与入群三开关（默认值＝旧行为：搜不到、直接进、邀请免审）
  searchable?: boolean
  auto_approve_join?: boolean
  approve_invites?: boolean
}

interface Props {
  group: GroupSettings | null
  onClose: () => void
  onUpdate: (updated: Partial<GroupSettings>) => void
  onLeave: () => void
}

type Tab = 'general' | 'members' | 'speak' | 'export'

export default function GroupSettingsPanel({ group, onClose, onUpdate, onLeave }: Props) {
  const t = useT()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('general')
  const [members, setMembers] = useState<GroupMember[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 表单状态
  const [name, setName] = useState(group?.name || '')
  const [announcement, setAnnouncement] = useState(group?.announcement || '')
  const [pinned, setPinned] = useState(false)
  const [speakLimit, setSpeakLimit] = useState(group?.speak_limit_per_minute === -1 ? -1 : (group?.speak_limit_per_minute || 0))
  const [speakWindow, setSpeakWindow] = useState(group?.speak_limit_window_seconds || 120)
  const [speakEnabled, setSpeakEnabled] = useState((group?.speak_limit_per_minute ?? 0) >= 0)
  const [concurrentAiLimit, setConcurrentAiLimit] = useState(group?.concurrent_ai_limit ?? 3)
  const [vectorAccel, setVectorAccel] = useState(group?.is_vector_accelerated || false)
  const [dndUntil, setDndUntil] = useState<string | null>(null)
  const [customDndMinutes, setCustomDndMinutes] = useState('')
  const [saving, setSaving] = useState(false)
  const [pausing, setPausing] = useState(false)

  // 头像设置状态
  const [avatarMode, setAvatarMode] = useState<'default' | 'members' | 'custom'>(group?.avatar_mode as any || 'default')
  const [includeAiAvatar, setIncludeAiAvatar] = useState(group?.include_ai_in_avatar ?? true)
  const [uploadingAvatar, setUploadingAvatar] = useState(false)
  const [avatarPreview, setAvatarPreview] = useState<string | null>(group?.avatar_url || null)
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false)

  // 发现与入群
  const [searchable, setSearchable] = useState(group?.searchable ?? false)
  const [autoApproveJoin, setAutoApproveJoin] = useState(group?.auto_approve_join ?? true)
  const [approveInvites, setApproveInvites] = useState(group?.approve_invites ?? false)
  const [pendingApprovals, setPendingApprovals] = useState(0)

  // 转让群主状态
  const [transferModalOpen, setTransferModalOpen] = useState(false)
  const [transferring, setTransferring] = useState(false)

  // 导出状态
  const [exportFormat, setExportFormat] = useState('json')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')

  const isOwner = group?.my_role === 'owner'
  const isAdmin = group?.my_role === 'admin' || isOwner
  const isAiOwned = group?.owner_type === 'ai'

  useEffect(() => {
    if (!group) return
    setName(group.name)
    setAnnouncement(group.announcement || '')
    // 从 group 获取 is_pinned（已经在 GET /groups 中返回）
    if (group.is_pinned !== undefined) {
      setPinned(group.is_pinned)
    }
    setSpeakLimit(group.speak_limit_per_minute || 0)
    setSpeakWindow(group.speak_limit_window_seconds || 120)
    setVectorAccel(group.is_vector_accelerated || false)
    setAvatarMode((group.avatar_mode as any) || 'default')
    setIncludeAiAvatar(group.include_ai_in_avatar ?? true)
    setAvatarPreview(group.avatar_url || null)
    setSearchable(group.searchable ?? false)
    setAutoApproveJoin(group.auto_approve_join ?? true)
    setApproveInvites(group.approve_invites ?? false)
    loadMembers()
    loadDndStatus()
    loadPendingApprovals()
  }, [group?.id])

  /** 本群待审批数：复用统一申请端点（审批权在后端已过滤），不为面板单开接口 */
  const loadPendingApprovals = async () => {
    if (!group) return
    try {
      const items = await fetchPendingRequests()
      setPendingApprovals(items.filter(r => r.kind !== 'friend' && r.group_id === group.id).length)
    } catch {
      // 拿不到数量不影响设置本身，静默
    }
  }

  const loadMembers = async () => {
    if (!group) return
    try {
      const data = await api.get(`/groups/${group.id}/members`)
      setMembers(data)
    } catch { /* ignore */ }
  }

  const loadDndStatus = async () => {
    if (!group) return
    try {
      // DND 状态从群列表中的 dnd_until 获取
      // 这里通过重新获取群列表来刷新
      const groups = await api.get('/groups')
      const g = groups.find((g: any) => g.id === group.id)
      if (g) setDndUntil(g.dnd_until || null)
    } catch { /* ignore */ }
  }

  const saveSettings = async (updates: Record<string, any>) => {
    if (!group) return
    setSaving(true)
    setError('')
    try {
      await api.patch(`/groups/${group.id}`, updates)
      onUpdate(updates)
      window.dispatchEvent(new CustomEvent('groupListRefresh'))
    } catch (e: any) {
      setError(e?.detail || t('error.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const handleSetDnd = async (minutes: number | null) => {
    if (!group) return
    try {
      await api.post(`/groups/${group.id}/dnd`, { group_id: group.id, duration_minutes: minutes })
      const until = minutes === null ? 'permanent' : new Date(Date.now() + minutes * 60_000).toISOString()
      setDndUntil(until)
    } catch (e: any) {
      setError(e?.detail || t('error.operationFailed'))
    }
  }

  const handleCustomDnd = async () => {
    if (!group) return
    const mins = parseInt(customDndMinutes, 10)
    if (isNaN(mins) || mins <= 0) {
      setError(t('error.invalidMinutes'))
      return
    }
    if (mins > 10080) {
      setError(t('error.dndMaxDuration'))
      return
    }
    await handleSetDnd(mins)
    setCustomDndMinutes('')
  }

  const handleCancelDnd = async () => {
    if (!group) return
    try {
      await api.post(`/groups/${group.id}/dnd/cancel`)
      setDndUntil(null)
    } catch (e: any) {
      setError(e?.detail || t('error.operationFailed'))
    }
  }

  const handleRoleChange = async (m: GroupMember, newRole: string) => {
    if (!group) return
    try {
      await api.patch(`/groups/${group.id}/members/${m.type}/${m.id}/role`, { role: newRole })
      setMembers(prev => prev.map(x => x.id === m.id && x.type === m.type ? { ...x, role: newRole } : x))
    } catch (e: any) {
      setError(e?.detail || t('error.operationFailed'))
    }
  }

  const handleKick = async (m: GroupMember) => {
    if (!group) return
    if (!confirm(t('groupSettings.confirmKick') + m.name + t('groupSettings.confirmKickEnd'))) return
    try {
      await api.delete(`/groups/${group.id}/members/${m.type}/${m.id}`)
      setMembers(prev => prev.filter(x => !(x.id === m.id && x.type === m.type)))
    } catch (e: any) {
      setError(e?.detail || t('error.operationFailed'))
    }
  }

  const handleLeave = async () => {
    if (!group) return
    if (!confirm(t('groupSettings.confirmLeave'))) return
    try {
      await api.post(`/groups/${group.id}/leave`)
      onLeave()
    } catch (e: any) {
      setError(e?.detail || t('error.leaveFailed'))
    }
  }

  const handleDisband = async () => {
    if (!group) return
    if (!confirm(t('groupSettings.confirmDisband'))) return
    try {
      await api.delete(`/groups/${group.id}`)
      onLeave()  // 复用退出逻辑：关闭面板 + 刷新列表
    } catch (e: any) {
      setError(e?.detail || t('error.operationFailed'))
    }
  }

  const handleTransferOwner = async (targetType: string, targetId: number) => {
    if (!group) return
    setTransferring(true)
    try {
      await api.post(`/groups/${group.id}/transfer-owner`, {
        member_type: targetType,
        member_id: targetId,
      })
      setTransferModalOpen(false)
      onUpdate({ owner_type: targetType, owner_id: targetId, my_role: 'admin' })
      loadMembers()
    } catch (e: any) {
      setError(e?.detail || '转让失败')
    } finally {
      setTransferring(false)
    }
  }

  // 头像上传完成回调（由 AvatarPickerModal 调用）
  const handleAvatarPickerUpload = async (blob: Blob) => {
    setUploadingAvatar(true)
    try {
      const ext =
        blob.type === 'image/gif'
          ? 'gif'
          : blob.type === 'image/png'
            ? 'png'
            : 'jpg'
      const file = new File([blob], `avatar.${ext}`, {
        type: blob.type || 'image/jpeg',
      })
      const formData = new FormData()
      formData.append('file', file)
      const res = await api.post<{ avatar_url: string; avatar_mode: string }>(
        `/groups/${group!.id}/avatar`,
        formData,
      )
      setAvatarPreview(res.avatar_url)
      setAvatarMode('custom')
      onUpdate({ avatar_url: res.avatar_url, avatar_mode: 'custom' })
      window.dispatchEvent(new CustomEvent('groupListRefresh'))
      window.dispatchEvent(new CustomEvent('groupAvatarChanged', {
        detail: { groupId: group!.id, avatar_url: res.avatar_url, avatar_mode: 'custom' },
      }))
    } finally {
      setUploadingAvatar(false)
    }
  }

  const handleExportChat = async () => {
    if (!group) return
    setExporting(true)
    setExportError('')
    try {
      const params = new URLSearchParams({ fmt: exportFormat })
      if (dateFrom) params.set('date_from', dateFrom)
      if (dateTo) params.set('date_to', dateTo)
      const ext = exportFormat === 'txt' ? 'txt' : exportFormat === 'html' ? 'html' : 'json'
      // 取回+落盘走唯一入口（以前这里手写 /api 前缀，嵌进 DSH 面板会 404）
      await api.download(`/groups/${group.id}/export?${params}`,
                         `chat_${group.name}_${new Date().toISOString().slice(0, 10)}.${ext}`)
    } catch (e: any) {
      setExportError(e.message)
    } finally {
      setExporting(false)
    }
  }

  if (!group) return null

  const tabs: { key: Tab; keyLabel: string; show: boolean }[] = [
    { key: 'general', keyLabel: 'groupSettings.tabGeneral', show: true },
    { key: 'members', keyLabel: 'groupSettings.tabMembers', show: true },
    { key: 'speak', keyLabel: 'groupSettings.tabSpeak', show: isAdmin },
    { key: 'export', keyLabel: 'groupSettings.tabExport', show: true },
  ]

  return (
    <div className="fixed inset-0 z-modal flex justify-end">
      {/* 点击外部关闭（桌面端） */}
      <div className="absolute inset-0 bg-black/30 hidden md:block" onClick={onClose} />

      <div className="relative w-full md:w-96 max-w-full h-full bg-surface md:border-l border-border shadow-2xl flex flex-col animate-slide-in">
        {/* 头部 */}
        <div className="h-14 px-4 border-b border-border flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="icon-btn-sm md:hidden -ml-1 text-textSecondary"
            >
              <ArrowLeft size={20} />
            </button>
            <h2 className="font-semibold text-sm text-textPrimary">{t('groupSettings.title')}</h2>
          </div>
          <button onClick={onClose} className="icon-btn-sm text-textMuted hidden md:block">
            <X size={16} />
          </button>
        </div>

        {/* Tab 切换 */}
        <div className="flex border-b border-border shrink-0">
          {tabs.filter(item => item.show).map(item => (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
                tab === item.key
                  ? 'text-primary-400 border-b-2 border-primary-400'
                  : 'text-textMuted hover:text-textSecondary'
              }`}
            >
              {t(item.keyLabel)}
            </button>
          ))}
        </div>

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 pb-[var(--safe-bottom)] md:pb-4">
          {error && (
            <div className="text-xs text-rose-400 bg-rose-400/10 rounded-control px-3 py-2">{error}</div>
          )}

          {/* === Tab: 基本设置 === */}
          {tab === 'general' && (
            <>
              {/* 群名称 */}
              <div>
                <label className="text-xs font-medium text-textSecondary">{t('groupSettings.groupName')}</label>
                <div className="flex gap-2 mt-1">
                  <input
                    value={name}
                    onChange={e => setName(e.target.value)}
                    disabled={!isAdmin}
                    className="flex-1 bg-elevated border border-border rounded-control px-3 py-2 text-sm text-textPrimary disabled:opacity-50 outline-none focus:border-primary-400"
                  />
                  {isAdmin && (
                    <button
                      onClick={() => saveSettings({ name })}
                      disabled={saving || name === group.name}
                      className="px-3 py-2 bg-primary-500 text-white rounded-control text-xs font-medium hover:bg-primary-600 disabled:opacity-50 transition-colors"
                    >
                      {t('common.save')}
                    </button>
                  )}
                </div>
              </div>

              {/* 群公告 */}
              <div>
                <label className="text-xs font-medium text-textSecondary">{t('groupSettings.announcement')}</label>
                {isAdmin ? (
                  <div className="mt-1 space-y-2">
                    <textarea
                      value={announcement}
                      onChange={e => setAnnouncement(e.target.value)}
                      placeholder={t('groupSettings.announcementPlaceholder')}
                      rows={3}
                      className="w-full bg-elevated border border-border rounded-control px-3 py-2 text-sm text-textPrimary outline-none focus:border-primary-400 resize-none"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => saveSettings({ announcement })}
                        disabled={saving}
                        className="px-3 py-1.5 bg-primary-500 text-white rounded-control text-xs font-medium hover:bg-primary-600 disabled:opacity-50 transition-colors"
                      >
                        {t('groupSettings.updateAnnouncement')}
                      </button>
                      {group.announcement && (
                        <button
                          onClick={async () => {
                            try {
                              await api.delete(`/groups/${group.id}/announcement`)
                              setAnnouncement('')
                              onUpdate({ announcement: null })
                            } catch (e: any) { setError(e?.detail || t('error.operationFailed')) }
                          }}
                          className="px-3 py-1.5 bg-rose-400/10 text-rose-400 rounded-control text-xs font-medium hover:bg-rose-400/20 transition-colors"
                        >
                          {t('groupSettings.deleteAnnouncement')}
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="mt-1 text-sm text-textSecondary bg-elevated rounded-control px-3 py-2">
                    {group.announcement || t('groupSettings.noAnnouncement')}
                  </p>
                )}
              </div>

              {isAdmin && (
              <>
              {/* 群头像设置 */}
              <div>
                <label className="text-xs font-medium text-textSecondary mb-3 block">{t('groupSettings.groupAvatar')}</label>
                <div className="grid grid-cols-3 gap-2">
                  {/* default 模式 */}
                  <button
                    onClick={() => { setAvatarMode('default'); saveSettings({ avatar_mode: 'default' }) }}
                    className={`relative p-3 rounded-card border text-center transition-colors ${
                      avatarMode === 'default'
                        ? 'border-primary-400 dark:border-primary-600 bg-primary-500/10 dark:bg-primary-900/40'
                        : 'border-border bg-elevated hover:bg-canvas'
                    }`}
                  >
                    <div className="w-10 h-10 mx-auto rounded-control bg-primary-500/10 dark:bg-primary-900/30 flex items-center justify-center mb-1.5">
                      <Users size={18} className="text-primary-400/60 dark:text-primary-300/60" />
                    </div>
                    <div className={`text-2xs font-medium ${avatarMode === 'default' ? 'text-textSecondary dark:text-primary-200' : 'text-textSecondary'}`}>{t('groupSettings.avatarModeDefault')}</div>
                    <div className={`text-3xs ${avatarMode === 'default' ? 'text-textMuted dark:text-primary-300/80' : 'text-textMuted'}`}>固定图标</div>
                    {avatarMode === 'default' && (
                      <CheckCircle2 size={14} className="absolute top-1.5 right-1.5 text-primary-400" />
                    )}
                  </button>

                  {/* members 模式 */}
                  <button
                    onClick={() => { setAvatarMode('members'); saveSettings({ avatar_mode: 'members', include_ai_in_avatar: includeAiAvatar }) }}
                    className={`relative p-3 rounded-card border text-center transition-colors ${
                      avatarMode === 'members'
                        ? 'border-primary-400 dark:border-primary-600 bg-primary-500/10 dark:bg-primary-900/40'
                        : 'border-border bg-elevated hover:bg-canvas'
                    }`}
                  >
                    <div className="w-10 h-10 mx-auto rounded-control bg-elevated flex items-center justify-center mb-1.5">
                      <Users size={18} className="text-textSecondary" />
                    </div>
                    <div className={`text-2xs font-medium ${avatarMode === 'members' ? 'text-textSecondary dark:text-primary-200' : 'text-textSecondary'}`}>{t('groupSettings.avatarModeMembers')}</div>
                    <div className={`text-3xs ${avatarMode === 'members' ? 'text-textMuted dark:text-primary-300/80' : 'text-textMuted'}`}>{t('common.grid')}</div>
                    {avatarMode === 'members' && (
                      <CheckCircle2 size={14} className="absolute top-1.5 right-1.5 text-primary-400" />
                    )}
                  </button>

                  {/* custom 模式 */}
                  <button
                    onClick={() => setAvatarMode('custom')}
                    className={`relative p-3 rounded-card border text-center transition-colors ${
                      avatarMode === 'custom'
                        ? 'border-primary-400 dark:border-primary-600 bg-primary-500/10 dark:bg-primary-900/40'
                        : 'border-border bg-elevated hover:bg-canvas'
                    }`}
                  >
                    <div className="w-10 h-10 mx-auto rounded-control overflow-hidden bg-elevated flex items-center justify-center mb-1.5">
                      {avatarPreview && avatarMode === 'custom' ? (
                        <img src={avatarPreview} alt="" className="w-full h-full object-cover" />
                      ) : (
                        <Image size={18} className="text-textMuted" />
                      )}
                    </div>
                    <div className={`text-2xs font-medium ${avatarMode === 'custom' ? 'text-textSecondary dark:text-primary-200' : 'text-textSecondary'}`}>{t('groupSettings.avatarModeCustom')}</div>
                    <div className={`text-3xs ${avatarMode === 'custom' ? 'text-textMuted dark:text-primary-300/80' : 'text-textMuted'}`}>{t('common.uploadImage')}</div>
                    {avatarMode === 'custom' && (
                      <CheckCircle2 size={14} className="absolute top-1.5 right-1.5 text-primary-400" />
                    )}
                  </button>
                </div>

                {/* members 模式的额外选项 */}
                {avatarMode === 'members' && isAdmin && (
                  <div className="flex items-center justify-between mt-3 px-1">
                    <div>
                      <div className="text-xs text-textPrimary font-medium">{t('groupSettings.includeAiInAvatar')}</div>
                      <div className="text-3xs text-textMuted">{t('groupSettings.includeAiInAvatarDesc')}</div>
                    </div>
                    <Toggle
                      checked={includeAiAvatar}
                      onChange={(next) => {
                        setIncludeAiAvatar(next)
                        saveSettings({ avatar_mode: 'members', include_ai_in_avatar: next })
                      }}
                    />
                  </div>
                )}

                {/* custom 模式的上传按钮 */}
                {avatarMode === 'custom' && isAdmin && (
                  <div className="mt-3">
                    <button
                      onClick={() => setAvatarPickerOpen(true)}
                      disabled={uploadingAvatar}
                      className="flex items-center justify-center gap-2 px-4 py-2.5 bg-elevated hover:bg-canvas border border-border border-dashed rounded-control w-full text-sm text-textSecondary hover:text-textPrimary transition-colors disabled:opacity-50"
                    >
                      <Camera size={16} />
                      {uploadingAvatar ? t('common.uploading') : t('groupSettings.customAvatar')}
                    </button>
                  </div>
                )}
              </div>

              </>
              )}
              {/* 置顶开关 */}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-textPrimary font-medium flex items-center gap-1.5">
                    <Pin size={14} className="text-textMuted shrink-0" />
                    置顶聊天
                  </div>
                  <div className="text-xs text-textMuted">置顶后出现在侧边栏顶部</div>
                </div>
                <Toggle
                  checked={pinned}
                  onChange={async (next) => {
                    try {
                      await api.post(`/groups/${group.id}/pin`, { is_pinned: next })
                      setPinned(next)
                      window.dispatchEvent(new CustomEvent('groupListRefresh'))
                      window.dispatchEvent(new CustomEvent('groupPinChanged', {
                        detail: { groupId: group.id, isPinned: next },
                      }))
                    } catch { /* ignore */ }
                  }}
                />
              </div>

              <hr className="border-border" />

              {/* 免打扰 */}
              <div>
                <h3 className="text-sm font-medium text-textPrimary flex items-center gap-2 mb-3">
                  <Bell size={14} className="text-textMuted" />
                  {t('groupSettings.dnd')}
                </h3>
                {dndUntil ? (
                  <div className="space-y-3">
                    <div className="bg-mint-400/10 text-mint-400 rounded-control px-3 py-2 text-xs flex items-center gap-2">
                      <BellOff size={14} />
                      {t('groupSettings.dndEnabled')}
                    </div>
                    <button
                      onClick={handleCancelDnd}
                      className="btn btn-md btn-primary w-full"
                    >
                      {t('groupSettings.dndCancel')}
                    </button>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <p className="text-xs text-textMuted">{t('groupSettings.dndHint')}</p>
                    <div className="grid grid-cols-2 gap-2">
                      {[
                        { key: 'groupSettings.dnd15min', minutes: 15 },
                        { key: 'groupSettings.dnd30min', minutes: 30 },
                        { key: 'groupSettings.dnd1hour', minutes: 60 },
                        { key: 'groupSettings.dnd4hours', minutes: 240 },
                        { key: 'groupSettings.dnd8hours', minutes: 480 },
                        { key: 'groupSettings.dndForever', minutes: null as unknown as number },
                      ].map((d) => (
                        <button
                          key={d.key}
                          onClick={() => handleSetDnd(d.minutes)}
                          className="flex items-center justify-center gap-1.5 px-3 py-2.5 bg-elevated hover:bg-primary-500/10 hover:text-primary-400 text-textSecondary border border-border rounded-control text-xs font-medium transition-colors"
                        >
                          <Clock size={12} />
                          {t(d.key)}
                        </button>
                      ))}
                    </div>
                    <div className="flex gap-2 items-center">
                      <input
                        type="number"
                        value={customDndMinutes}
                        onChange={(e) => setCustomDndMinutes(e.target.value)}
                        placeholder={t('groupSettings.dndCustomPlaceholder')}
                        min={1}
                        max={10080}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleCustomDnd() }}
                        className="flex-1 bg-elevated border border-border rounded-control px-3 py-2 text-sm text-textPrimary outline-none focus:border-primary-400 placeholder:text-textMuted"
                      />
                      <button
                        onClick={handleCustomDnd}
                        disabled={!customDndMinutes.trim()}
                        className="btn btn-xs btn-primary shrink-0"
                      >
                        {t('common.set')}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* 向量加速（仅管理员可见） */}
              {isAdmin && (
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm text-textPrimary font-medium">{t('groupSettings.vectorAccel')}</div>
                    <div className="text-xs text-textMuted">
                      {isAiOwned ? t('groupSettings.vectorAccelAiOwned') : t('groupSettings.vectorAccelHybridSearch')}
                    </div>
                  </div>
                  <Toggle checked={vectorAccel} onChange={(next) => { setVectorAccel(next); saveSettings({ is_vector_accelerated: next }) }} />
                </div>
              )}

              {/* 发现与入群（仅管理员可见） */}
              {isAdmin && (
                <div className="space-y-3">
                  <div className="text-sm text-textPrimary font-medium">{t('groupSettings.discovery')}</div>

                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs text-textSecondary">{t('groupSettings.searchable')}</div>
                      <div className="text-3xs text-textMuted">{t('groupSettings.searchableHint')}</div>
                    </div>
                    <Toggle
                      checked={searchable}
                      onChange={(next) => { setSearchable(next); saveSettings({ searchable: next }) }}
                    />
                  </div>

                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs text-textSecondary">{t('groupSettings.autoApproveJoin')}</div>
                      <div className="text-3xs text-textMuted">{t('groupSettings.autoApproveJoinHint')}</div>
                    </div>
                    <Toggle
                      checked={autoApproveJoin}
                      onChange={(next) => { setAutoApproveJoin(next); saveSettings({ auto_approve_join: next }) }}
                    />
                  </div>

                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs text-textSecondary">{t('groupSettings.approveInvites')}</div>
                      <div className="text-3xs text-textMuted">{t('groupSettings.approveInvitesHint')}</div>
                    </div>
                    <Toggle
                      checked={approveInvites}
                      onChange={(next) => { setApproveInvites(next); saveSettings({ approve_invites: next }) }}
                    />
                  </div>

                  {/* 审批入口：审批权在后端，这里只把人送到申请列表 */}
                  <button
                    onClick={() => navigate('/list?tab=requests')}
                    className="w-full flex items-center justify-between px-3 py-2 rounded-control bg-elevated hover:bg-canvas text-xs text-textSecondary transition-colors"
                  >
                    <span>{t('groupSettings.pendingApprovals')}</span>
                    <span className={pendingApprovals > 0 ? 'text-primary-400 font-medium' : 'text-textMuted'}>
                      {pendingApprovals}
                    </span>
                  </button>
                </div>
              )}

              {/* 联邦共享（仅管理员可见） */}
              {isAdmin && <FederationShareSection groupId={group!.id} />}

              {/* 暂停对话（仅管理员） */}
              {isAdmin && (
                <button
                  onClick={async () => {
                    setPausing(true)
                    try {
                      const r = await api.post(`/groups/${group.id}/toggle-pause`)
                      onUpdate({ is_paused: (r as any).is_paused } as Partial<GroupSettings>)
                    } catch (e: any) { setError(e?.detail || "操作失败") } finally { setPausing(false) }
                  }}
                  className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-control text-sm font-medium transition-colors disabled:opacity-50 ${group?.is_paused ? "bg-mint-400/10 text-mint-400 hover:bg-mint-400/20" : "bg-accent-400/10 text-accent-400 hover:bg-accent-400/20"}`}
                >
                  {pausing ? <Loader2 size={16} className="animate-spin" /> : <Pause size={16} />}
                  {group?.is_paused ? "恢复对话" : "暂停对话"}
                </button>
              )}

              <hr className="border-border" />

              {/* 转让群主（仅群主可见） */}
              {isOwner && (
                <>
                  <button
                    onClick={() => {
                      if (members.filter(m => m.role === 'admin' || m.role === 'member').length === 0) {
                        alert(t('groupSettings.noTransferTarget'))
                        return
                      }
                      setTransferModalOpen(true)
                    }}
                    disabled={members.filter(m => m.role === 'admin' || m.role === 'member').length === 0}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary-500/10 text-primary-400 rounded-control text-sm font-medium hover:bg-primary-500/20 border border-primary-400/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    <Crown size={16} />
                    {t('groupSettings.transferOwner')}
                  </button>

                  {/* 转让确认弹窗 */}
                  {transferModalOpen && (
                    <div className="fixed inset-0 z-toast flex items-center justify-center">
                      <div className="absolute inset-0 bg-black/40" onClick={() => setTransferModalOpen(false)} />
                      <div className="relative bg-surface border border-border rounded-card shadow-2xl p-4 w-80 max-w-[90vw]">
                        <h3 className="text-sm font-semibold text-textPrimary mb-3">{t('groupSettings.transferOwnerTitle')}</h3>
                        <p className="text-xs text-textMuted mb-3">{t('groupSettings.transferOwnerHint')}</p>
                        <div className="space-y-1 max-h-48 overflow-y-auto mb-3">
                          {members.filter(m => m.role !== 'owner').map(m => (
                            <button
                              key={`${m.type}:${m.id}`}
                              onClick={() => {
                                if (confirm(`${t('groupSettings.confirmTransfer')}「${m.name}」？`)) {
                                  handleTransferOwner(m.type, m.id)
                                }
                              }}
                              disabled={transferring}
                              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-control hover:bg-elevated transition-colors disabled:opacity-50"
                            >
                              <span className={`w-2 h-2 rounded-full shrink-0 ${getStateDotColor(m.state)}`} />
                              <span className="text-sm text-textPrimary truncate">{m.name}</span>
                              {m.role === 'admin' && (
                                <span className="text-3xs text-primary-400 ml-auto shrink-0">{t('groupSettings.roleAdmin')}</span>
                              )}
                            </button>
                          ))}
                        </div>
                        <button
                          onClick={() => setTransferModalOpen(false)}
                          className="w-full px-3 py-2 text-xs text-textMuted hover:text-textSecondary rounded-control hover:bg-elevated transition-colors"
                        >
                          {t('common.cancel')}
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* 退群 */}
              <button
                onClick={handleLeave}
                disabled={isOwner && !group.name.startsWith('DM:')}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-rose-400/10 text-rose-400 rounded-control text-sm font-medium hover:bg-rose-400/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                title={isOwner && !group.name.startsWith('DM:') ? t('groupSettings.ownerLeaveHint') : t('groupSettings.leaveGroup')}
              >
                <LogOut size={16} />
                {isOwner && !group.name.startsWith('DM:') ? t('groupSettings.ownerCannotLeave') : t('groupSettings.leaveGroup')}
              </button>

              {/* 解散群聊（仅群主可见） */}
              {isOwner && (
                <button
                  onClick={handleDisband}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-rose-500/10 text-rose-500 rounded-control text-sm font-medium hover:bg-rose-500/20 transition-colors border border-rose-500/20"
                >
                  {t('groupSettings.disbandGroup')}
                </button>
              )}
            </>
          )}

          {/* === Tab: 成员管理 === */}
          {tab === 'members' && (
            <>
              <div className="flex items-center justify-between">
                <span className="text-sm text-textPrimary font-medium">
                  {t('groupSettings.memberCount')} ({members.length})
                </span>
                <button
                  onClick={() => {
                    // 触发父组件的邀请弹窗
                    const event = new CustomEvent('open-invite-modal')
                    window.dispatchEvent(event)
                  }}
                  className="flex items-center gap-1 px-2 py-1 text-xs text-primary-400 hover:bg-primary-400/10 rounded-control transition-colors"
                >
                  <UserPlus size={14} />
                  {t('groupSettings.invite')}
                </button>
              </div>

              <div className="space-y-1">
                {members.map(m => (
                  <div
                    key={`${m.type}:${m.id}`}
                    className="flex items-center justify-between px-3 py-2.5 rounded-control hover:bg-elevated transition-colors"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      {/* 状态圆点 */}
                      <span className={`w-2 h-2 rounded-full shrink-0 ${getStateDotColor(m.state)}`} />
                      <div className="min-w-0">
                        <div className="text-sm text-textPrimary truncate flex items-center gap-1.5">
                          {m.name}
                          {m.role === 'owner' && (
                            <span className="text-3xs text-accent-400 font-medium flex items-center gap-0.5"><Crown size={10} />{t('groupSettings.roleOwner')}</span>
                          )}
                          {m.type === 'ai' && (
                            <span className="text-3xs text-primary-400 font-medium">{t('chatlist.ai')}</span>
                          )}
                        </div>
                        <div className="text-3xs text-textMuted">
                          {m.role === 'owner' ? t('groupSettings.roleOwner') : m.role === 'admin' ? t('groupSettings.roleAdmin') : t('groupSettings.roleMember')}
                          {m.dnd_until && ' · ' + t('dm.shortDnd')}
                        </div>
                      </div>
                    </div>

                    {/* 操作按钮（群主/管理员可见，不可操作自己） */}
                    {isAdmin && m.role !== 'owner' && (
                      <div className="flex items-center gap-1 shrink-0 ml-2">
                        {isOwner && (
                          <button
                            onClick={() => handleRoleChange(m, m.role === 'admin' ? 'member' : 'admin')}
                            className="p-1 rounded hover:bg-elevated text-textMuted hover:text-primary-400 transition-colors"
                            title={m.role === 'admin' ? t('groupSettings.demoteToMember') : t('groupSettings.promoteToAdmin')}
                          >
                            {m.role === 'admin' ? <ShieldOff size={14} /> : <Shield size={14} />}
                          </button>
                        )}
                        <button
                          onClick={() => handleKick(m)}
                          className="p-1 rounded hover:bg-rose-400/10 text-textMuted hover:text-rose-400 transition-colors"
                          title={t('groupSettings.kick')}
                        >
                          <UserX size={14} />
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* === Tab: AI 发言限制（仅管理员） === */}
          {tab === 'speak' && isAdmin && (
            <>
              {isAiOwned ? (
                <div className="text-center py-8">
                  <Volume2 size={32} className="mx-auto text-mint-400 mb-3" />
                  <p className="text-sm text-textPrimary font-medium">{t('groupSettings.aiManagedGroup')}</p>
                  <p className="text-xs text-textMuted mt-1">
                    {t('groupSettings.aiManagedDesc1')}<br />
                    {t('groupSettings.aiManagedDesc2')}
                  </p>
                </div>
              ) : (
                <>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-xs font-medium text-textSecondary">
                        {t('groupSettings.speakLimit')}
                      </label>
                      <span className="text-xs text-primary-400 font-medium">
                        {speakLimit === 0 ? t('groupSettings.unlimited') : `${speakLimit} ${t('groupSettings.perMinute')}`}
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={60}
                      value={speakLimit}
                      onChange={e => setSpeakLimit(Number(e.target.value))}
                      className="w-full accent-primary-500"
                    />
                    <div className="flex justify-between text-3xs text-textMuted">
                      <span>{t('groupSettings.unlimitedLabel')}</span>
                      <span>60</span>
                    </div>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-xs font-medium text-textSecondary">
                        {t('groupSettings.speakWindow')}
                      </label>
                      <span className="text-xs text-primary-400 font-medium">{speakWindow}s</span>
                    </div>
                    <input
                      type="range"
                      min={30}
                      max={600}
                      step={30}
                      value={speakWindow}
                      onChange={e => setSpeakWindow(Number(e.target.value))}
                      className="w-full accent-primary-500"
                    />
                    <div className="flex justify-between text-3xs text-textMuted">
                      <span>30s</span>
                      <span>600s</span>
                    </div>
                  </div>

                  {/* 预览 */}
                  <div className="bg-elevated rounded-control px-3 py-2.5 text-xs text-textSecondary space-y-1">
                    <div className="font-medium text-textPrimary">{t('groupSettings.preview')}</div>
                    {speakLimit > 0 ? (
                      <div>
                        {t('groupSettings.speakPreviewPer')} {speakWindow} {t('groupSettings.speakPreviewAllow')} <span className="text-primary-400 font-medium">{speakLimit * 2}</span> {t('groupSettings.speakPreviewRounds')}
                        <br />
                        <span className="text-textMuted">
                          {t('groupSettings.speakPreviewBufferNote')}
                        </span>
                      </div>
                    ) : (
                      <div>{t('groupSettings.speakPreviewUnlimitedDesc')}</div>
                    )}
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-xs font-medium text-textSecondary">AI 并发数</label>
                      <span className="text-xs text-primary-400 font-medium">{concurrentAiLimit}</span>
                    </div>
                    <input type="range" min={1} max={10} value={concurrentAiLimit} onChange={e => setConcurrentAiLimit(Number(e.target.value))} className="w-full accent-primary-500" />
                    <div className="flex justify-between text-3xs text-textMuted"><span>1</span><span>10</span></div>
                  </div>


                  <button
                    onClick={() => saveSettings({
                      speak_limit_per_minute: speakEnabled ? speakLimit : -1,
                      concurrent_ai_limit: concurrentAiLimit,
                      speak_limit_window_seconds: speakWindow,
                    })}
                    disabled={saving}
                    className="w-full px-4 py-2.5 bg-primary-500 text-white rounded-control text-sm font-medium hover:bg-primary-600 disabled:opacity-50 transition-colors"
                  >
                    {saving ? t('common.saving') : t('groupSettings.saveSpeakLimit')}
                  </button>
                </>
              )}
            </>
          )}

          {/* === Tab: 导出记录 === */}
          {tab === 'export' && (
            <>
              <div>
                <label className="text-xs font-medium text-textSecondary">{t('groupSettings.exportFormat')}</label>
                <div className="flex gap-2 mt-1">
                  {[
                    { key: 'json', labelKey: 'JSON', descKey: 'groupSettings.exportJsonDesc' },
                    { key: 'txt', labelKey: 'TXT', descKey: 'groupSettings.exportTxtDesc' },
                    { key: 'html', labelKey: 'HTML', descKey: 'groupSettings.exportHtmlDesc' },
                  ].map(f => (
                    <button
                      key={f.key}
                      onClick={() => setExportFormat(f.key)}
                      className={`flex-1 py-3 px-2 rounded-card border text-center transition-colors ${
                        exportFormat === f.key
                          ? 'border-primary-400 bg-primary-500/10 text-primary-600 dark:text-primary-300'
                          : 'border-border bg-canvas text-textSecondary hover:bg-elevated'
                      }`}
                    >
                      <div className="text-sm font-medium">{f.labelKey}</div>
                      <div className="text-3xs text-textMuted">{t(f.descKey)}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs font-medium text-textSecondary">{t('groupSettings.dateRange')}</label>
                <div className="flex items-center gap-2 mt-1">
                  <input
                    type="date"
                    value={dateFrom}
                    onChange={e => setDateFrom(e.target.value)}
                    className="flex-1 bg-elevated border border-border rounded-control px-3 py-2 text-sm text-textPrimary"
                  />
                  <span className="text-textMuted text-xs">{t('common.to')}</span>
                  <input
                    type="date"
                    value={dateTo}
                    onChange={e => setDateTo(e.target.value)}
                    className="flex-1 bg-elevated border border-border rounded-control px-3 py-2 text-sm text-textPrimary"
                  />
                </div>
              </div>

              <button
                onClick={handleExportChat}
                disabled={exporting}
                className="btn btn-md btn-primary w-full gap-2"
              >
                <Download size={16} />
                {exporting ? t('common.exporting') : t('groupSettings.downloadExport')}
              </button>

              {exportError && <div className="text-xs text-rose-400">{exportError}</div>}
            </>
          )}
        </div>
      </div>

      {/* 头像选择弹窗 */}
      {avatarPickerOpen && group && (
        <AvatarPickerModal
          onUpload={handleAvatarPickerUpload}
          onClose={() => setAvatarPickerOpen(false)}
          cropShape="rect"
        />
      )}
    </div>
  )
}
