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

/**
 * 群聊邀请卡片。
 *
 * **自己不画壳**——和转发的 channel 卡片同一条规矩：外面那层气泡就是卡片的外框，
 * 里面再套一层圆角边框就成了「框里套框」（用户看到的就是这个）。
 * 所以这里只排内容：一行身份（图标 + 谁邀请 + 群名 + 状态），下面是动作。
 */
export default function InvitationCard({
  invitationId, groupName, inviterName, message, status,
  onAccept, onReject, isMine,
}: InvitationCardProps) {
  const t = useT()

  const isResolved = status !== 'pending'

  // 文字颜色跟着气泡走：自己的消息是深色气泡，对方的浅色
  const txt = isMine ? 'text-white/90' : 'text-textPrimary'
  const txtSec = isMine ? 'text-white/60' : 'text-textSecondary'

  return (
    <div className="min-w-[220px] max-w-[340px] text-left">
      {/* 身份行 */}
      <div className="flex items-center gap-2">
        <div className={`
          w-8 h-8 rounded-full flex items-center justify-center shrink-0
          ${isResolved
            ? isMine ? 'bg-white/15 text-white/70' : 'bg-border text-textMuted'
            : isMine ? 'bg-white/20 text-white' : 'bg-mint-400/20 text-mint-500'}
        `}>
          {status === 'accepted' ? <Check size={16} /> :
           status === 'rejected' ? <X size={16} /> :
           <Users size={16} />}
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium ${txt} truncate`}>
            {t('invitation:title').replace('{inviter}', inviterName)}
          </p>
          <p className={`text-xs ${txtSec} truncate`}>
            {t('invitation:groupLabel')}：<span className={`${txt} font-medium`}>{groupName}</span>
          </p>
        </div>
        {isResolved && (
          <span className={`
            shrink-0 text-xs px-2 py-0.5 rounded-full font-medium
            ${status === 'accepted'
              ? 'bg-mint-100 dark:bg-mint-900/30 text-mint-600 dark:text-mint-400'
              : 'bg-rose-100 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400'}
          `}>
            {status === 'accepted' ? t('invitation:accepted') : t('invitation:rejected')}
          </span>
        )}
      </div>

      {/* 附言 */}
      {message && (
        <p className={`mt-1.5 text-xs ${txtSec} italic`}>"{message}"</p>
      )}

      {/* 动作：和卡片正文之间一条发丝线，同转发卡片的页脚 */}
      {!isResolved && (
        <div className={`mt-2.5 pt-2 flex gap-2 border-t ${isMine ? 'border-white/25' : 'border-border'}`}>
          <button
            onClick={(e) => { e.stopPropagation(); onAccept(invitationId) }}
            className="btn btn-sm btn-success flex-1"
          >
            <Check size={14} />
            {t('invitation:accept')}
          </button>
          {/* ui-standard-ignore: 邀请卡片内拒绝按钮，底色随卡片明暗切换（isMine），无对应 .btn 变体 */}
          <button
            onClick={(e) => { e.stopPropagation(); onReject(invitationId) }}
            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5
              ${isMine ? 'bg-white/10 hover:bg-white/20 text-white/80' : 'bg-canvas hover:bg-elevated text-textSecondary hover:text-textPrimary'}
              text-sm rounded-control border ${isMine ? 'border-white/20' : 'border-border'} transition-colors`}
          >
            <X size={14} />
            {t('invitation:reject')}
          </button>
        </div>
      )}
    </div>
  )
}
