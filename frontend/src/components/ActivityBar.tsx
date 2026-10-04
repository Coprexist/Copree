import { useT } from '../i18n/I18nContext'

/** 弹跳三点（与 MessageBubble 共用样式） */
const BouncingDots = ({ className = '' }: { className?: string }) => (
  <span className={`inline-flex gap-0.5 ${className}`}>
    <span className="w-1 h-1 bg-current rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
    <span className="w-1 h-1 bg-current rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
    <span className="w-1 h-1 bg-current rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
  </span>
)

export interface ActivityUser {
  id: number
  name: string
  avatarUrl: string | null
  status: 'thinking' | 'typing'
}

interface ActivityBarProps {
  users: ActivityUser[]
}

export default function ActivityBar({ users }: ActivityBarProps) {
  const t = useT()
  if (users.length === 0) return null

  const MAX = 3
  const hasTyping = users.some(u => u.status === 'typing')
  const statusLabel = hasTyping ? t('chat:typing') : t('chat:thinking')
  const overflow = users.length > MAX

  // 最多展示 MAX 个头像，溢出时最后一个显示省略号
  const shown = overflow ? users.slice(0, MAX - 1) : users.slice(0, MAX)

  return (
    <div className="sticky bottom-0 z-20 flex items-center gap-2.5 px-4 py-2.5 bg-canvas/90 backdrop-blur border-t border-border animate-in slide-in-from-bottom-2 duration-200">
      {/* 头像堆叠（半遮挡） */}
      <div className="flex -space-x-2.5 shrink-0">
        {shown.map((u) => (
          <div
            key={u.id}
            className="relative w-7 h-7 rounded-full ring-2 ring-canvas overflow-hidden"
            title={u.name}
          >
            {u.avatarUrl ? (
              <>
                <div className="absolute inset-px rounded-full bg-gradient-to-br from-primary-500 to-primary-700/30" />
                <img src={u.avatarUrl} alt={u.name} className="relative w-full h-full object-cover" loading="lazy" />
              </>
            ) : (
              <span className="w-full h-full flex items-center justify-center text-3xs font-bold bg-gradient-to-br from-primary-500 to-primary-700 text-white">
                {u.name.charAt(0).toUpperCase()}
              </span>
            )}
          </div>
        ))}
        {overflow && (
          <div className="relative w-7 h-7 rounded-full ring-2 ring-canvas bg-surface flex items-center justify-center text-3xs font-bold text-textMuted">
            …
          </div>
        )}
      </div>

      {/* 状态文字 */}
      <span className="text-xs text-textSecondary truncate">
        {users.length === 1 ? (
          <>{users[0].name} <span className="text-textMuted">{statusLabel}</span></>
        ) : (
          <>{users.map(u => u.name).join('、')} <span className="text-textMuted">等{users.length}人 {statusLabel}</span></>
        )}
      </span>

      {/* 三点弹跳气泡 */}
      <span className="shrink-0 px-2.5 py-1.5 bg-surface border border-border rounded-card text-primary-400">
        <BouncingDots />
      </span>
    </div>
  )
}
