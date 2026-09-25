import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useT, useLang } from '../i18n/I18nContext'
import { api } from '../api/client'
import { Dialog, PageShell } from '../components/ui'
import ExternalLinkSafe from '../components/ExternalLinkSafe'
import { AI_TYPE_LABEL } from '../constants'
import { fmtTokenNum } from '../utils/format'
import { getStatusTextStyle, STATUS_COLORS, BG_SURFACE_LIGHT, BG_SURFACE_DARK } from '../utils/statusColor'
import VerificationCodeInput from '../components/VerificationCodeInput'
import { useTheme } from '../context/ThemeContext'
import {
  User, Settings, LogOut, Shield,
  Gift, BarChart3, Bot, ChevronRight, Edit3,
  Loader2, Check, X, ArrowRight, Activity,
  FileText, HardDrive, Camera, Users, MessageSquare, Share2, Github
} from 'lucide-react'
import AvatarPickerModal from '../components/AvatarPickerModal'

interface AgentBrief {
  id: number
  name: string
  state: string
  chat_model: string | null
  avatar_url: string | null
  ai_type: string
}

interface UsageOverview {
  agent_id: number
  agent_name: string
  model: string | null
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  reasoning_tokens: number
  cached_tokens: number
  total_calls: number
}

function StatCard({ icon, value, label, onClick, bg }: {
  icon: React.ReactNode; value: string | number; label: string; onClick: () => void; bg: string;
}) {
  return (
    <button onClick={onClick} className={`rounded-card p-3.5 text-center hover:brightness-95 transition-all cursor-pointer w-full ${bg}`}>
      <div className="mx-auto mb-1 flex justify-center">{icon}</div>
      <div className="text-lg font-bold text-textPrimary tabular-nums">{value}</div>
      <div className="text-3xs text-textMuted mt-0.5">{label}</div>
    </button>
  )
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function MePage() {
  const { user, logout, refreshUser } = useAuth()
  const t = useT()
  const lang = useLang()
  const { theme } = useTheme()
  const navigate = useNavigate()

  const [agents, setAgents] = useState<AgentBrief[]>([])
  const [usage, setUsage] = useState<UsageOverview[]>([])
  const [usageLoading, setUsageLoading] = useState(true)
  const [redeemCode, setRedeemCode] = useState('')
  const [redeemMsg, setRedeemMsg] = useState('')
  const [redeeming, setRedeeming] = useState(false)

  // 存储概览
  const [storage, setStorage] = useState<{ total_used: number; total_files: number; quota_mb: number; usage_percent: number } | null>(null)
  const [storageLoading, setStorageLoading] = useState(true)
  const [stats, setStats] = useState<{ ai_count: number; friend_count: number; group_count: number; storage_used: number } | null>(null)

  // 编辑资料弹窗
  const [showEditProfile, setShowEditProfile] = useState(false)
  const [editUsername, setEditUsername] = useState('')
  const [editPassword, setEditPassword] = useState('')
  const [editBio, setEditBio] = useState('')
  const [editStatusText, setEditStatusText] = useState('')
  const [editStatusColor, setEditStatusColor] = useState('')
  const [editAvatarUrl, setEditAvatarUrl] = useState('')
  const [editSaving, setEditSaving] = useState(false)
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false)

  // 文件列表 + 转发


  // v0.2.0 邮箱绑定
  const [showBindEmail, setShowBindEmail] = useState(false)
  const [bindEmail, setBindEmail] = useState('')
  const [bindCode, setBindCode] = useState('')
  const [bindCodeSent, setBindCodeSent] = useState(false)
  const [bindSendCooldown, setBindSendCooldown] = useState(0)
  const [bindError, setBindError] = useState('')
  const [bindLoading, setBindLoading] = useState(false)
  const { rebindEmail, removeEmail } = useAuth()

  // GitHub 绑定（商城同步以用户身份推送）
  const [ghBind, setGhBind] = useState<{ bound: boolean; username: string | null }>({ bound: false, username: null })
  const [showBindGithub, setShowBindGithub] = useState(false)
  const [ghToken, setGhToken] = useState('')
  const [ghBinding, setGhBinding] = useState(false)
  const [ghError, setGhError] = useState('')

  const doBindGithub = async () => {
    if (!ghToken.trim()) return
    setGhBinding(true); setGhError('')
    try {
      const r = await api.post<{ bound: boolean; username: string }>('/market/github/bind', { token: ghToken.trim() })
      setGhBind(r); setShowBindGithub(false); setGhToken('')
      window.dispatchEvent(new Event('gh-bind-changed'))  // 通知商城页同步
    } catch (e: any) {
      setGhError(e?.message || String(e))
    } finally {
      setGhBinding(false)
    }
  }

  const doUnbindGithub = async () => {
    if (!confirm('解绑 GitHub 账户？（商城同步将回退为管理员 token）')) return
    try {
      await api.delete('/market/github/bind')
      setGhBind({ bound: false, username: null })
      window.dispatchEvent(new Event('gh-bind-changed'))  // 通知商城页同步
    } catch { /* ignore */ }
  }

  // 支持 /me?bind=github 从商城页跳转过来直接打开绑定弹窗
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('bind') === 'github') {
      setShowBindGithub(true)
      // 清理 URL 参数，避免刷新后重复弹
      window.history.replaceState({}, '', '/me')
    }
  }, [])



  useEffect(() => {
    // 加载上传限制（头像选择时检查大小，每次进 MePage 刷新）
    api.get('/user/config/upload-limits').then((limits: any) => {
      sessionStorage.setItem('upload_limits', JSON.stringify({ ...limits, ts: Date.now() }))
    }).catch(() => {})

    // GitHub 绑定状态
    api.get<{ bound: boolean; username: string | null }>('/market/github/bind').then((r) => {
      setGhBind(r || { bound: false, username: null })
    }).catch(() => {})

    // 加载我的 AI 列表
    api.get<AgentBrief[]>('/agents').then(r => {
      setAgents((Array.isArray(r) ? r : []).slice(0, 3))
    }).catch(() => {})

    // 加载用量概览
    setUsageLoading(true)
    api.get<UsageOverview[]>('/conversation-log/usage/overview?days=30').then(r => {
      setUsage(Array.isArray(r) ? r : [])
    }).catch(() => {}).finally(() => setUsageLoading(false))

    // 加载存储概览
    setStorageLoading(true)
    api.get<{ total_used: number; total_files: number; quota_mb: number; usage_percent: number }>('/user/storage').then(r => {
      setStorage(r)
    }).catch(() => {}).finally(() => setStorageLoading(false))

    // 加载个人统计（单次高效 COUNT 查询）
    api.get<{ ai_count: number; friend_count: number; group_count: number; storage_used: number }>('/user/stats').then(r => {
      setStats(r)
    }).catch(() => {})
  }, [])

  // 汇总
  const totalTokens = usage.reduce((s, u) => s + (u.total_tokens || 0), 0)
  const totalCalls = usage.reduce((s, u) => s + (u.total_calls || 0), 0)
  const totalReasoning = usage.reduce((s, u) => s + (u.reasoning_tokens || 0), 0)
  const totalCached = usage.reduce((s, u) => s + (u.cached_tokens || 0), 0)
  const cacheRate = totalTokens > 0 ? Math.round(totalCached / totalTokens * 100) : 0

  // 上线天数
  const daysSince = user?.created_at
    ? Math.max(1, Math.floor((Date.now() - new Date(user.created_at).getTime()) / 86400000))
    : 1

  // ── 兑换码 ──
  const handleRedeem = async () => {
    if (!redeemCode.trim()) return
    setRedeeming(true)
    setRedeemMsg('')
    try {
      const res = await api.post<{ message: string }>('/user/redeem', { code: redeemCode.trim().toUpperCase() })
      setRedeemMsg(res.message || t('common.redeemSuccess'))
      setRedeemCode('')
      refreshUser?.()
    } catch (err: any) {
      setRedeemMsg(err.message || t('common.redeemFailed'))
    } finally { setRedeeming(false) }
  }

  // 存储区滚动引用
  const storageRef = useRef<HTMLDivElement>(null)
  const scrollToStorage = useCallback(() => {
    storageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])
  useEffect(() => {
    if (bindSendCooldown <= 0) return
    const timer = setInterval(() => setBindSendCooldown(c => c - 1), 1000)
    return () => clearInterval(timer)
  }, [bindSendCooldown])

  const handleSendBindCode = async () => {
    if (!bindEmail || bindSendCooldown > 0) return
    setBindError('')
    try {
      await api.post('/auth/send-verification-code', { email: bindEmail, purpose: 'rebind' })
      setBindCodeSent(true)
      setBindSendCooldown(60)
    } catch (err: any) {
      setBindError(err.message || t('common.error'))
    }
  }

  const handleConfirmBind = async () => {
    if (!bindEmail || !bindCode) return
    setBindLoading(true)
    setBindError('')
    try {
      await rebindEmail(bindEmail, bindCode)
      setShowBindEmail(false)
      setBindEmail('')
      setBindCode('')
      setBindCodeSent(false)
    } catch (err: any) {
      setBindError(err.message || t('common.error'))
    } finally { setBindLoading(false) }
  }

  const handleRemoveEmail = async () => {
    if (!confirm(t('auth.removeEmail') + '?')) return
    try {
      await removeEmail()
    } catch (err: any) {
      alert(err.message || t('common.error'))
    }
  }

  // ── 编辑资料 ──
  const openEditProfile = () => {
    setEditUsername(user?.username || '')
    setEditPassword('')
    setEditBio(user?.bio || '')
    setEditStatusText(user?.status_text || '')
    setEditStatusColor(user?.status_color || '')
    setEditAvatarUrl(user?.avatar_url || '')
    setShowEditProfile(true)
  }
  const handleAvatarPickerUpload = async (blob: Blob) => {
    const ext = blob.type === 'image/gif' ? 'gif' : blob.type === 'image/png' ? 'png' : 'jpg'
    const res = await api.upload('/user/avatar', new File([blob], `avatar.${ext}`, { type: blob.type || 'image/jpeg' }))
    setEditAvatarUrl(res.avatar_url)
    refreshUser?.()
  }
  const handleSaveProfile = async () => {
    setEditSaving(true)
    try {
      const body: any = {}
      if (editUsername && editUsername !== user?.username) body.username = editUsername
      if (editPassword) body.password = editPassword
      if (editBio !== (user?.bio || '')) body.bio = editBio
      if (editStatusText !== (user?.status_text || '')) body.status_text = editStatusText
      if (editStatusColor !== (user?.status_color || '')) body.status_color = editStatusColor || null
      if (editAvatarUrl !== (user?.avatar_url || '')) body.avatar_url = editAvatarUrl
      if (Object.keys(body).length > 0) {
        await api.put('/user/settings', body)
      }
      await refreshUser?.()
      setShowEditProfile(false)
    } catch (err: any) {
      alert(err.message || t('error.saveFailed'))
    } finally { setEditSaving(false) }
  }

  if (!user) return null

  return (
    <PageShell title={t('me.title')} width="content" contentClassName="space-y-5">

      {/* ====== 个人资料卡 ====== */}
      <div className="bg-surface rounded-dialog border border-border p-5">
        <div className="flex items-center gap-4">
          {/* 头像 */}
          <div className="shrink-0">
            {user.avatar_url ? (
              <div className="relative w-16 h-16 rounded-full overflow-hidden shadow shadow-primary-500/25">
                <div className="absolute inset-px rounded-full bg-gradient-to-br from-primary-500 to-primary-700/30" />
                <img src={user.avatar_url} className="relative w-full h-full rounded-full object-cover" />
              </div>
            ) : (
              <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center shadow shadow-primary-500/25">
                <User size={28} className="text-white" />
              </div>
            )}
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold text-textPrimary truncate">{user.username}</h2>
              {user.role === 'admin' && (
                <span className="chip chip-accent shrink-0">{t('me.adminBadge')}</span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-1 text-xs text-textMuted">
              <span className="flex items-center gap-1"><Bot size={12} /> AI {agents.length}</span>
              <span>{t('me.daysOnline')} {daysSince} {t('me.daysSuffix')}</span>
              {user.status_text && (
                <span className="font-medium" style={user.status_color
                  ? getStatusTextStyle(user.status_color, theme === 'dark' ? BG_SURFACE_DARK : BG_SURFACE_LIGHT)
                  : undefined}>
                  {user.status_text}
                </span>
              )}
            </div>
            <button
              onClick={openEditProfile}
              className="mt-2 text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 flex items-center gap-1 transition-colors"
            >
              <Edit3 size={12} /> {t('me.editProfile')}
            </button>
            {/* 邮箱 */}
            <div className="mt-3 pt-3 border-t border-border/60">
              <span className="text-3xs text-textMuted uppercase tracking-wider">{t('auth.email')}</span>
              <div className="flex items-center gap-2 mt-1">
                {user?.email ? (
                  <>
                    <span className="text-sm text-textPrimary truncate">{user.email}</span>
                    {user.email_verified ? (
                      <span className="chip chip-mint shrink-0">{t('auth.emailVerified')}</span>
                    ) : (
                      <span className="chip chip-accent shrink-0">{t('auth.emailNotVerified')}</span>
                    )}
                  </>
                ) : (
                  <span className="text-sm text-textMuted">{t('auth.noEmailBound')}</span>
                )}
                <button
                  onClick={() => setShowBindEmail(true)}
                  className="text-3xs text-primary-400 hover:text-primary-500 transition-colors"
                >
                  {user?.email ? t('auth.changeEmail') : t('auth.bindEmailTitle')}
                </button>
                {user?.email && (
                  <button
                    onClick={handleRemoveEmail}
                    className="text-3xs text-rose-400 hover:text-rose-500 dark:hover:text-rose-300 transition-colors"
                  >
                    {t('auth.removeEmail')}
                  </button>
                )}
              </div>
            </div>
            {/* GitHub（商城同步身份） */}
            <div className="mt-3 pt-3 border-t border-border/60">
              <span className="text-3xs text-textMuted uppercase tracking-wider">GitHub</span>
              <div className="flex items-center gap-2 mt-1">
                <Github size={13} className="text-textMuted shrink-0" />
                {ghBind.bound ? (
                  <>
                    <span className="text-sm text-textPrimary truncate">@{ghBind.username}</span>
                    <span className="chip chip-mint shrink-0">已绑定</span>
                  </>
                ) : (
                  <span className="text-sm text-textMuted">未绑定</span>
                )}
                <button
                  onClick={() => setShowBindGithub(true)}
                  className="text-3xs text-primary-400 hover:text-primary-500 transition-colors"
                >
                  {ghBind.bound ? '更换' : '绑定'}
                </button>
                {ghBind.bound && (
                  <button
                    onClick={doUnbindGithub}
                    className="text-3xs text-rose-400 hover:text-rose-500 dark:hover:text-rose-300 transition-colors"
                  >
                    解绑
                  </button>
                )}
              </div>
              <div className="text-3xs text-textMuted mt-1">用于世界商城同步，以你的身份推送</div>
            </div>
          </div>
        </div>

        {/* 个人统计概览 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 pt-4 border-t border-border/60">
          <StatCard
            icon={<Bot size={18} className="text-primary-400" />}
            value={stats?.ai_count ?? '...'}
            label={t('me.aiCountCard')}
            bg="bg-primary-500/5"
            onClick={() => navigate('/agents')}
          />
          <StatCard
            icon={<Users size={18} className="text-mint-400" />}
            value={stats?.friend_count ?? '...'}
            label={t('me.friendCountCard')}
            bg="bg-mint-500/5"
            onClick={() => navigate('/list')}
          />
          <StatCard
            icon={<MessageSquare size={18} className="text-accent-400" />}
            value={stats?.group_count ?? '...'}
            label={t('me.groupCountCard')}
            bg="bg-accent-500/5"
            onClick={() => navigate('/chat')}
          />
          <StatCard
            icon={<HardDrive size={18} className="text-accent-400" />}
            value={stats ? formatSize(stats.storage_used) : '...'}
            label={t('me.storageUsedCard')}
            bg="bg-accent-500/5"
            onClick={scrollToStorage}
          />
        </div>
      </div>

      {/* ====== 我的 AI ====== */}
      <div className="bg-surface rounded-dialog border border-border p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <Bot size={16} className="text-primary-400" /> {t('me.myAiSection')}
          </h3>
          <Link to="/agents" className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 flex items-center gap-1 transition-colors">
            {t('me.viewAll')} <ArrowRight size={12} />
          </Link>
        </div>
        {agents.length === 0 ? (
          <p className="text-sm text-textMuted py-3 text-center">{t('me.noAiLink')}<Link to="/agents" className="text-primary-400">{t('me.goCreate')}</Link></p>
        ) : (
          <div className="flex gap-3 overflow-x-auto pb-1">
            {agents.map(a => (
              <Link
                key={a.id}
                to={`/agents/${a.id}`}
                className="shrink-0 w-28 bg-canvas rounded-card p-3 border border-border hover:border-primary-400/30 transition-colors text-center"
              >
                {a.avatar_url ? (
                  <div className="relative w-10 h-10 rounded-full mx-auto mb-1.5 shadow shadow-primary-500/15 overflow-hidden">
                    <div className="absolute inset-px rounded-full bg-gradient-to-br from-primary-500 to-primary-700/30" />
                    <img src={a.avatar_url} className="relative w-full h-full rounded-full object-cover" />
                  </div>
                ) : (
                  <div className="w-10 h-10 rounded-full bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center mx-auto mb-1.5 shadow shadow-primary-500/15">
                    <Bot size={18} className="text-white" />
                  </div>
                )}
                <div className="text-xs font-medium text-textPrimary truncate">{a.name}</div>
                <div className="text-3xs text-textMuted mt-0.5">{a.state === 'active' ? t('me.stateActive') : a.state === 'dnd' ? t('me.stateDnd') : t('me.stateOffline')}</div>
                {(AI_TYPE_LABEL[a.ai_type]) && (
                  <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded-full mt-1 font-medium ${AI_TYPE_LABEL[a.ai_type].cls}`}>
                    {t(AI_TYPE_LABEL[a.ai_type].key)}
                  </span>
                )}
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* ====== API 用量 ====== */}
      <div className="bg-surface rounded-dialog border border-border p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <BarChart3 size={16} className="text-primary-400" /> {t('me.apiUsage30d')}
          </h3>
          <Link to="/me/usage" className="text-xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 flex items-center gap-1 transition-colors">
            {t('me.viewDetails')} <ArrowRight size={12} />
          </Link>
        </div>
        {usageLoading ? (
          <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin text-textMuted" /></div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { key: 'me.totalTokens', value: fmtTokenNum(totalTokens, lang), icon: Activity, color: 'text-primary-400' },
              { key: 'me.calls', value: totalCalls, icon: BarChart3, color: 'text-mint-400' },
              { key: 'me.cacheHitRate', value: `${cacheRate}%`, icon: FileText, color: 'text-accent-400' },
              { key: 'me.thinkingTokens', value: fmtTokenNum(totalReasoning, lang), icon: Activity, color: 'text-accent-400' },
            ].map(item => (
              <div key={item.key} className="bg-canvas rounded-card p-3 text-center">
                <item.icon size={16} className={`${item.color} mx-auto mb-1`} />
                <div className="text-sm font-semibold text-textPrimary">{item.value}</div>
                <div className="text-3xs text-textMuted">{t(item.key)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ====== 存储概览 ====== */}
      <a href="/me/storage" className="block bg-surface rounded-dialog border border-border p-5 hover:bg-elevated transition-colors">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-textPrimary flex items-center gap-2">
            <HardDrive size={16} className="text-primary-400" /> {t('me.storageSection')}
          </h3>
          <span className="text-xs text-primary-400">查看存储 →</span>
        </div>
        {storageLoading ? (
          <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin text-textMuted" /></div>
        ) : storage ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-textMuted">
              <span>{t('me.used')} {storage.total_used >= 1048576 ? `${(storage.total_used / 1048576).toFixed(1)}MB` : `${(storage.total_used / 1024).toFixed(0)}KB`}</span>
              <span className={storage.usage_percent > 90 ? 'text-rose-400 font-medium' : storage.usage_percent > 70 ? 'text-accent-400 font-medium' : ''}>
                {storage.usage_percent}%
              </span>
            </div>
            <div className="w-full h-2 bg-canvas rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  storage.usage_percent > 90 ? 'bg-rose-400' : storage.usage_percent > 70 ? 'bg-accent-400' : 'bg-primary-400'
                }`}
                style={{ width: `${Math.min(storage.usage_percent, 100)}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-3xs text-textMuted">
              <span>{storage.total_files} {t('me.fileCountSuffix')}</span>
              <span>{t('me.quota')} {storage.quota_mb}MB</span>
            </div>
            {storage.usage_percent > 90 && (
              <p className="text-xs text-rose-400">{t('me.storageWarning')}</p>
            )}
          </div>
        ) : (
          <p className="text-sm text-textMuted py-3 text-center">{t('me.noStorageData')}</p>
        )}
      </a>

      {/* ====== 兑换码 ====== */}
      <div id="redeem-section" className="bg-surface rounded-dialog border border-border p-5">
        <h3 className="text-sm font-semibold text-textPrimary mb-3 flex items-center gap-2">
          <Gift size={16} className="text-primary-400" /> {t('me.redeemSection')}
        </h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={redeemCode}
            onChange={e => setRedeemCode(e.target.value)}
            placeholder={t('me.redeemPlaceholder')}
            className="flex-1 px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 font-mono"
          />
          <button
            onClick={handleRedeem}
            disabled={redeeming || !redeemCode.trim()}
            className="btn btn-sm btn-primary"
          >
            {redeeming ? <Loader2 size={14} className="animate-spin" /> : t('me.redeemButton')}
          </button>
        </div>
        {redeemMsg && (
          <p className={`text-xs mt-2 ${redeemMsg.includes('失败') || redeemMsg.includes('无效') ? 'text-rose-400' : 'text-mint-400'}`}>
            {redeemMsg}
          </p>
        )}
      </div>

      {/* ====== 管理员入口 ====== */}
      {user.role === 'admin' && (
        <div className="bg-surface rounded-dialog border border-border">
          <Link
            to="/admin"
            className="flex items-center gap-3 px-5 py-3 hover:bg-elevated transition-colors"
          >
            <Shield size={16} className="text-accent-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-sm text-textPrimary">{t('me.managementSection')}</div>
              <div className="text-xs text-textMuted">{t('me.managementDesc')}</div>
            </div>
            <ChevronRight size={14} className="text-textMuted shrink-0" />
          </Link>
        </div>
      )}

      {/* ====== 设置入口 ====== */}
      <Link
        to="/settings"
        className="bg-surface rounded-dialog border border-border p-5 flex items-center gap-3 hover:bg-elevated transition-colors"
      >
        <Settings size={18} className="text-textMuted shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-textPrimary">{t('me.settings')}</div>
        </div>
        <ChevronRight size={14} className="text-textMuted shrink-0" />
      </Link>

      {/* ====== 退出登录 ====== */}
      <button
        onClick={logout}
        className="w-full py-3 rounded-card border border-rose-500/20 text-rose-400 hover:bg-rose-500/5 text-sm font-medium transition-colors flex items-center justify-center gap-2"
      >
        <LogOut size={14} /> {t('me.logout')}
      </button>

      {/* ====== 编辑资料弹窗 ====== */}
      {showEditProfile && (
        <Dialog onClose={() =>  setShowEditProfile(false)} className="flex items-center justify-center">
          <div
            className="bg-surface rounded-dialog border border-border w-full max-w-sm mx-4 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h3 className="text-sm font-semibold text-textPrimary">{t('me.editProfileModalTitle')}</h3>
              <button onClick={() => setShowEditProfile(false)} className="p-1 rounded-control hover:bg-elevated text-textMuted">
                <X size={16} />
              </button>
            </div>
            <div className="p-5 space-y-4">
              {/* 头像 */}
              <div className="flex flex-col items-center gap-2">
                <div className="w-20 h-20 rounded-full bg-primary-500/10 flex items-center justify-center overflow-hidden border-2 border-border">
                  {editAvatarUrl ? (
                    <img src={editAvatarUrl} className="w-full h-full object-cover" />
                  ) : (
                    <User size={32} className="text-primary-400" />
                  )}
                </div>
                <button
                  onClick={() => setAvatarPickerOpen(true)}
                  className="flex items-center gap-1 text-xs text-primary-400 hover:text-primary-500 transition-colors"
                >
                  <Camera size={12} />
                  {t('me.changeAvatar')}
                </button>
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('me.usernameField')}</label>
                <input
                  type="text"
                  value={editUsername}
                  onChange={e => setEditUsername(e.target.value)}
                  placeholder={user.username}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('me.bioField')}</label>
                <textarea
                  value={editBio}
                  onChange={e => setEditBio(e.target.value)}
                  placeholder={t('me.bioPlaceholder')}
                  rows={3}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50 resize-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('me.statusTextField')}</label>
                <input
                  type="text"
                  value={editStatusText}
                  onChange={e => setEditStatusText(e.target.value)}
                  placeholder={t('me.statusTextPlaceholder')}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary placeholder:text-textMuted focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
                <p className="text-3xs text-textMuted mt-1">{editStatusText.length} 字</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('me.statusColorLabel')}</label>
                <div className="flex items-center gap-2 flex-wrap">
                  {STATUS_COLORS.map(c => (
                    <button
                      key={c.value}
                      type="button"
                      onClick={() => setEditStatusColor(c.value)}
                      className={`w-6 h-6 rounded-full border-2 transition-all ${
                        editStatusColor === c.value
                          ? 'border-primary-400 scale-110 shadow-md'
                          : c.value === ''
                            ? 'border-border bg-canvas hover:border-textMuted'
                            : 'border-transparent hover:scale-105'
                      }`}
                      style={c.value ? { backgroundColor: c.value } : undefined}
                      title={c.label}
                    >
                      {c.value === '' && <X size={10} className="text-textMuted m-auto" />}
                    </button>
                  ))}
                  <div className="relative">
                    <input
                      type="color"
                      value={editStatusColor || '#000000'}
                      onChange={e => setEditStatusColor(e.target.value)}
                      className="w-6 h-6 rounded-full cursor-pointer border-2 border-border hover:border-primary-400 transition-colors"
                      title={t('me.statusColorCustom')}
                    />
                  </div>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-textSecondary mb-1">{t('me.passwordField')}</label>
                <input
                  type="password"
                  value={editPassword}
                  onChange={e => setEditPassword(e.target.value)}
                  placeholder={t('me.passwordMinHint')}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-sm text-textPrimary focus:outline-none focus:ring-2 focus:ring-primary-500/50"
                />
              </div>
              <button
                onClick={handleSaveProfile}
                disabled={editSaving}
                className="btn btn-md btn-primary w-full gap-2"
              >
                {editSaving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {editSaving ? t('me.savingProfile') : t('me.saveButton')}
              </button>
            </div>
          </div>
        </Dialog>
      )}

      {/* 头像裁剪弹窗 */}
      {avatarPickerOpen && (
        <AvatarPickerModal
          onUpload={handleAvatarPickerUpload}
          onClose={() => setAvatarPickerOpen(false)}
          title={t('me.changeAvatar')}
        />
      )}

      {/* 文件转发弹窗 */}


      {/* v0.2.0 邮箱绑定弹窗 */}
      {showBindEmail && (
        <Dialog onClose={() =>  setShowBindEmail(false)} className="flex items-center justify-center p-4">
          <div className="bg-surface border border-border rounded-dialog p-6 w-full max-w-sm shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-textPrimary mb-4">{t('auth.bindEmailTitle')}</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-textSecondary mb-1">{t('auth.email')}</label>
                <input
                  type="email"
                  value={bindEmail}
                  onChange={e => { setBindEmail(e.target.value); setBindCodeSent(false) }}
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-textPrimary text-sm focus:outline-none focus:ring-2 focus:ring-primary-500/60"
                  placeholder={t('auth.emailPlaceholder')}
                />
              </div>
              <button
                type="button"
                onClick={handleSendBindCode}
                disabled={!bindEmail || bindSendCooldown > 0}
                className="w-full py-2 text-sm font-medium rounded-card border border-primary-500/30 text-primary-500 hover:bg-primary-500/10 disabled:opacity-40 transition-colors"
              >
                {bindSendCooldown > 0
                  ? t('auth.codeResendIn').replace('{seconds}', String(bindSendCooldown))
                  : bindCodeSent ? t('auth.codeSent') : t('auth.sendCode')
                }
              </button>
              {bindCodeSent && (
                <div>
                  <label className="block text-xs text-textSecondary mb-2 text-center">{t('auth.codePlaceholder')}</label>
                  <VerificationCodeInput
                    value={bindCode}
                    onChange={setBindCode}
                    disabled={bindLoading}
                  />
                </div>
              )}
              {bindError && (
                <div className="text-sm text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-card px-3 py-2">{bindError}</div>
              )}
              <div className="flex gap-2 pt-2">
                <button onClick={() => setShowBindEmail(false)} className="flex-1 py-2 text-sm rounded-card border border-border text-textSecondary hover:text-textPrimary transition-colors">{t('common.cancel')}</button>
                <button
                  onClick={handleConfirmBind}
                  disabled={!bindEmail || !bindCode || bindLoading}
                  className="btn btn-sm btn-primary flex-1"
                >
                  {bindLoading ? t('auth.verifying') : t('common.confirm')}
                </button>
              </div>
            </div>
          </div>
        </Dialog>
      )}

      {/* GitHub 绑定弹窗 */}
      {showBindGithub && (
        <Dialog onClose={() =>  setShowBindGithub(false)} className="flex items-center justify-center p-4">
          <div className="bg-surface border border-border rounded-dialog p-6 w-full max-w-sm shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-textPrimary mb-1">绑定 GitHub 账户</h3>
            <p className="text-xs text-textMuted mb-4">用于世界商城同步——以你的身份推送到 Copree-Community。Token 加密存储，仅本实例可见。</p>
            <div className="space-y-3">
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs text-textSecondary">GitHub Token（classic 或 fine-grained，需仓库写权限）</label>
                  <ExternalLinkSafe href="https://github.com/settings/tokens/new" className="text-3xs text-primary-400 hover:text-primary-500 dark:hover:text-primary-300 transition-colors shrink-0">去 GitHub 生成 token →</ExternalLinkSafe>
                </div>
                <input
                  type="password"
                  value={ghToken}
                  onChange={e => setGhToken(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') doBindGithub() }}
                  placeholder="ghp_… / github_pat_…"
                  className="w-full px-3 py-2 rounded-card border border-border bg-canvas text-textPrimary text-sm focus:outline-none focus:ring-2 focus:ring-primary-500/60"
                />
              </div>
              {ghError && (
                <div className="text-sm text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-card px-3 py-2">{ghError}</div>
              )}
              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => setShowBindGithub(false)}
                  className="flex-1 py-2 text-sm rounded-card border border-border text-textSecondary hover:bg-elevated transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={doBindGithub}
                  disabled={!ghToken.trim() || ghBinding}
                  className="btn btn-sm btn-primary flex-1"
                >
                  {ghBinding ? '绑定中…' : '绑定'}
                </button>
              </div>
            </div>
          </div>
        </Dialog>
      )}
    </PageShell>
  )
}
