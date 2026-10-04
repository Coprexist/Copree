import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import ChatView from './ChatView'
import DMSettingsPanel from './DMSettingsPanel'
import ProfileCard from './ProfileCard'
import { ArrowLeft, Bell, BellOff, Settings, Bot, User, Globe, ShieldAlert } from 'lucide-react'
import { getStateDotColor } from '../constants'
import { formatMessageTime } from '../utils/time'
import { useLang } from '../i18n/I18nContext'
import { useT } from '../i18n/I18nContext'

interface DMChatViewProps {
  sessionId: string
  onMobileBack?: () => void
}

export default function DMChatView({ sessionId, onMobileBack }: DMChatViewProps) {
  const t = useT()
  const lang = useLang()
  const [partner, setPartner] = useState<{ id: number; name: string; type: string; state: string | null; last_active_at?: string | null; is_federated?: boolean; avatar_url?: string | null } | null>(null)
  const [myDndUntil, setMyDndUntil] = useState<string | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showProfile, setShowProfile] = useState(false)
  const [tokenUsage, setTokenUsage] = useState<{ total_tokens: number; api_calls: number } | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (!sessionId) return
    setPartner(null)
    setMyDndUntil(null)
    setTokenUsage(null)
    // 先加载会话元数据（标题栏）—— 跳过消息加载，等 ChatView 就绪后再加载
    api.get(`/dm/${sessionId}?summary=true`).then((data) => {
      setPartner(data.partner)
      setMyDndUntil(data.my_dnd_until || null)
    }).catch(console.error)
    // 并行加载 token 用量
    api.get(`/dm/${sessionId}/my-token-usage`).then(setTokenUsage).catch(() => {})
  }, [sessionId])

  // 监听 DM 对方状态变化（后端 state_change 广播）→ 实时更新头部状态点
  useEffect(() => {
    const handler = (e: Event) => {
      const d = (e as CustomEvent).detail
      if (!d || typeof d.user_id !== 'number') return
      setPartner((prev) => {
        if (!prev || prev.id !== d.user_id) return prev
        return { ...prev, state: d.state, last_active_at: d.last_active_at ?? prev.last_active_at }
      })
    }
    window.addEventListener('dm-partner-state-change', handler)
    return () => window.removeEventListener('dm-partner-state-change', handler)
  }, [])

  // 监听到新消息时刷新 token 用量
  const refreshTokenUsage = () => {
    api.get(`/dm/${sessionId}/my-token-usage`).then(setTokenUsage).catch(() => {})
  }

  const handleToggleDnd = async () => {
    try {
      if (myDndUntil) {
        await api.post(`/dm/${sessionId}/dnd/cancel`)
        setMyDndUntil(null)
      } else {
        await api.post(`/dm/${sessionId}/dnd`, { duration_minutes: null })
        setMyDndUntil('permanent')
      }
    } catch { /* ignore */ }
  }

  const stateColor = getStateDotColor(partner?.state)
  const isActive = partner?.state === 'active'

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* 私信头部 — 与群聊头部布局对齐 */}
      <div className="px-4 h-14 border-b border-border bg-surface flex items-center gap-2 shrink-0">
        {/* 移动端：打开会话列表 */}
        <button
          onClick={() => navigate('/chat')}
          className="md:hidden p-1.5 -ml-1 rounded-control hover:bg-elevated text-textSecondary transition-colors"
          title={t('dm:sessionList')}
        >
          <ArrowLeft size={20} />
        </button>

        {/* 对方头像（可点击 → 资料卡） */}
        <button
          onClick={() => partner && setShowProfile(true)}
          className="shrink-0"
          title={t('profileCard:viewProfile')}
        >
          {partner?.avatar_url ? (
            <div className="relative w-8 h-8 rounded-full overflow-hidden shrink-0">
              <div className={`absolute inset-px rounded-full bg-gradient-to-bl ${
                partner?.type === 'system' ? 'from-rose-400 to-rose-600' : 'from-teal-400 to-teal-600'
              }`} />
              <img src={partner.avatar_url} alt={partner.name || ''} className="relative w-full h-full rounded-full object-cover" loading="lazy" />
            </div>
          ) : (
            <div className={`w-8 h-8 rounded-full bg-gradient-to-bl flex items-center justify-center text-xs font-bold text-white overflow-hidden ${
              partner?.type === 'system' ? 'from-rose-400 to-rose-600' : 'from-teal-400 to-teal-600'
            }`}>
          {partner?.type === 'system' ? (
            <ShieldAlert size={14} className="text-white" />
          ) : (
            partner?.name?.charAt(0)?.toUpperCase() || '?'
          )}
        </div>
          )}
        </button>

        {/* 对方信息 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-textPrimary text-sm truncate">
              {partner?.name || t('dm:loading')}
            </span>
          </div>
          <span className="text-3xs text-textMuted">
            {partner?.type === 'system' ? <><ShieldAlert size={12} className="inline text-rose-400" /> 系统通知</>
            : partner?.type === 'ai' ? <><Bot size={12} className="inline" /> {t('dm:ai')}</>
            : <><User size={12} className="inline" /> {t('dm:user')}</>}
            {partner?.type !== 'system' && isActive && ` · ${t('dm:online')}`}
            {partner?.type !== 'system' && partner?.state === 'dnd' && ` · ${t('dm:dnd')}`}
            {partner?.type !== 'system' && (!partner?.state || partner?.state === 'inactive') && ` · ${t('dm:offline')}`}
            {tokenUsage && tokenUsage.total_tokens > 0 && (
              <span className="ml-2 text-textMuted">
                · {tokenUsage.total_tokens >= 1000 ? `${(tokenUsage.total_tokens / 1000).toFixed(1)}k` : tokenUsage.total_tokens} tokens
              </span>
            )}
          </span>
        </div>

        {/* 联邦标签 */}
        {partner?.is_federated && (
          <span className="chip chip-primary shrink-0"
                title={t('chat:federatedGroup')}>
            <Globe size={11} />
            {t('chat:federated')}
          </span>
        )}

        {/* 在线状态指示 */}
        <span className={`inline-flex items-center gap-1 text-3xs font-medium ${isActive ? 'text-mint-400' : partner?.state === 'dnd' ? 'text-rose-400' : 'text-textMuted'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${stateColor}`} />
          {isActive ? t('dm:online') : partner?.state === 'dnd' ? t('dm:shortDnd') : partner?.last_active_at ? `${t('dm:lastActive')} ${formatMessageTime(partner.last_active_at, lang)}` : t('dm:offline')}
        </span>

        {/* 免打扰按钮 */}
        <button
          onClick={handleToggleDnd}
          className={`p-1 rounded-control transition-colors ${
            myDndUntil
              ? 'text-rose-400 hover:bg-rose-400/10'
              : 'text-textMuted hover:text-rose-400 hover:bg-elevated'
          }`}
          title={myDndUntil ? t('dm:unmute') : t('dm:mute')}
        >
          {myDndUntil ? <BellOff size={14} /> : <Bell size={14} />}
        </button>

        {/* 设置按钮（与群聊头部的 Settings 对齐） */}
        <button
          onClick={() => setShowSettings(true)}
          className="p-1 rounded-control hover:bg-elevated text-textMuted hover:text-textSecondary transition-colors"
          title={t('dm:dmSettings')}
        >
          <Settings size={14} />
        </button>
      </div>

      <ChatView key={sessionId} conversationType="dm" conversationId={sessionId} />

      {/* 私信设置面板 */}
      {showSettings && (
        <DMSettingsPanel
          sessionId={sessionId}
          partner={partner}
          myDndUntil={myDndUntil}
          onClose={() => setShowSettings(false)}
          onDndChange={(dndUntil) => setMyDndUntil(dndUntil)}
        />
      )}

      {/* 资料卡 */}
      {showProfile && partner && (
        <ProfileCard
          entityType={partner.type as 'human' | 'ai'}
          entityId={partner.id}
          entityName={partner.name}
          state={partner.state || undefined}
          onClose={() => setShowProfile(false)}
        />
      )}
    </div>
  )
}
