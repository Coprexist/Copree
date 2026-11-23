import { useState, useEffect, type CSSProperties } from 'react'
import { api } from '../api/client'
import ChatView from './ChatView'
import ChatHeader from './shared/ChatHeader'
import { CHAT_HEADER_INSET } from './shared/chatHeaderInset'
import DMSettingsPanel from './DMSettingsPanel'
import ProfileCard from './ProfileCard'
import { Bell, BellOff, Settings, Globe, ShieldAlert } from 'lucide-react'
import { getStateDotColor } from '../constants'
import { avatarGradient } from '../utils/avatar'
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
    <div
      className="flex-1 flex flex-col min-h-0 overflow-hidden relative"
      style={{ '--header-inset': CHAT_HEADER_INSET } as CSSProperties}
    >
      {/* 头部与群聊共用一份（shared/ChatHeader），消息从条底下滚过去 */}
      <ChatHeader
        onBack={onMobileBack}
        backTitle={t('dm:sessionList')}
        avatar={
          <button onClick={() => partner && setShowProfile(true)} className="shrink-0" title={t('profileCard:viewProfile')}>
            {partner?.avatar_url ? (
              <div className="relative w-8 h-8 rounded-full overflow-hidden">
                <div className={'absolute inset-px rounded-full ' + avatarGradient(partner.type, false, true)} />
                <img src={partner.avatar_url} alt={partner.name || ''} className="relative w-full h-full rounded-full object-cover" loading="lazy" />
              </div>
            ) : (
              <div className={'w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white overflow-hidden ' + avatarGradient(partner?.type, false, false)}>
                {partner?.type === 'system'
                  ? <ShieldAlert size={14} className="text-white" />
                  : (partner?.name?.charAt(0)?.toUpperCase() || '?')}
              </div>
            )}
          </button>
        }
        title={partner?.name || t('dm:loading')}
        chips={partner?.is_federated ? (
          <span className="chip chip-primary shrink-0" title={t('chat:federatedGroup')}>
            <Globe size={11} />
            {t('chat:federated')}
          </span>
        ) : null}
        status={
          // 类型 + 状态 + 用量收成一行：头顶只有一条副标题，排两行在 56px 里会挤
          <span className={'inline-flex items-center gap-1 ' + (isActive ? 'text-mint-400' : partner?.state === 'dnd' ? 'text-rose-400' : 'text-textMuted')}>
            <span className={'w-1.5 h-1.5 rounded-full ' + stateColor} />
            {partner?.type === 'system' ? '系统通知' : partner?.type === 'ai' ? t('dm:ai') : t('dm:user')}
            {' · '}
            {isActive ? t('dm:online') : partner?.state === 'dnd' ? t('dm:shortDnd')
              : partner?.last_active_at ? t('dm:lastActive') + ' ' + formatMessageTime(partner.last_active_at, lang) : t('dm:offline')}
            {tokenUsage && tokenUsage.total_tokens > 0
              && ' · ' + (tokenUsage.total_tokens >= 1000 ? (tokenUsage.total_tokens / 1000).toFixed(1) + 'k' : tokenUsage.total_tokens) + ' tokens'}
          </span>
        }
        actions={<>
          <button
            onClick={handleToggleDnd}
            className={'icon-btn' + (myDndUntil ? ' is-alert' : '')}
            title={myDndUntil ? t('dm:unmute') : t('dm:mute')}
          >
            {myDndUntil ? <BellOff size={16} /> : <Bell size={16} />}
          </button>
          <button onClick={() => setShowSettings(true)} className="icon-btn" title={t('dm:dmSettings')}>
            <Settings size={16} />
          </button>
        </>}
      />
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
