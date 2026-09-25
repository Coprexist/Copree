import { Check, X, Users } from 'lucide-react'
import { useT } from '../i18n/I18nContext'

interface InvitationCardProps {
  invitationId: number
  groupName: string
  inviterName: string
  message?: string
  status: 'pending' | 'accepted' | 'rejected'
  onAccept: (invitationId: number) => void
  onReject: (invitationId: number) => void
  isMine?: boolean
}

export default function InvitationCard({
  invitationId, groupName, inviterName, message, status,
  onAccept, onReject, isMine,
}: InvitationCardProps) {
  const t = useT()

  const isResolved = status !== 'pending'

  // 深色气泡（自己发的）用白字 + 半透明背景
  const txt = isMine ? 'text-white/90' : 'text-textPrimary'
  const txtSec = isMine ? 'text-white/60' : 'text-textSecondary'
  const cardBg = isMine
    ? 'bg-white/10 border-white/20'
    : isResolved
      ? 'border-border bg-canvas/50'
      : 'border-mint-400/40 bg-mint-50/30 dark:bg-mint-900/10'

  return (
    <div className={`rounded-card border-2 overflow-hidden transition-all ${cardBg}`}>
      {/* 头部 */}
      <div className="flex items-center gap-2 px-4 pt-3 pb-2">
        <div className={`
          w-8 h-8 rounded-full flex items-center justify-center
          ${isResolved
            ? 'bg-border text-textMuted'
            : isMine ? 'bg-white/20 text-white' : 'bg-mint-400/20 text-mint-500'}
        `}>
          {status === 'accepted' ? <Check size={16} /> :
           status === 'rejected' ? <X size={16} /> :
           <Users size={16} />}
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium ${txt} truncate`}>
            {t('invitation.title').replace('{inviter}', inviterName)}
          </p>
          <p className={`text-xs ${txtSec} truncate`}>
            {t('invitation.groupLabel')}：<span className={`${txt} font-medium`}>{groupName}</span>
          </p>
        </div>
        {/* 状态标签 */}
        {isResolved && (
          <span className={`
            shrink-0 text-xs px-2 py-0.5 rounded-full font-medium
            ${status === 'accepted'
              ? 'bg-mint-100 dark:bg-mint-900/30 text-mint-600 dark:text-mint-400'
              : 'bg-rose-100 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400'}
          `}>
            {status === 'accepted' ? t('invitation.accepted') : t('invitation.rejected')}
          </span>
        )}
      </div>

      {/* 附言 */}
      {message && (
        <div className="px-4 pb-1">
          <p className={`text-xs ${txtSec} italic`}>"{message}"</p>
        </div>
      )}

      {/* 按钮区（仅 pending） */}
      {!isResolved && (
        <div className="flex gap-2 px-4 pb-3 pt-1">
          <button
            onClick={(e) => { e.stopPropagation(); onAccept(invitationId) }}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5
              bg-mint-500 hover:bg-mint-600 text-white text-sm font-medium
              rounded-control transition-colors"
          >
            <Check size={14} />
            {t('invitation.accept')}
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onReject(invitationId) }}
            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5
              ${isMine ? 'bg-white/10 hover:bg-white/20 text-white/80' : 'bg-canvas hover:bg-elevated text-textSecondary hover:text-textPrimary'}
              text-sm rounded-control border ${isMine ? 'border-white/20' : 'border-border'} transition-colors`}
          >
            <X size={14} />
            {t('invitation.reject')}
          </button>
        </div>
      )}
    </div>
  )
}
