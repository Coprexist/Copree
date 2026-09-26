import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AtSign, Bell, CheckCircle2, Mail, Megaphone, MessageSquare, UserPlus, Users, X, XCircle,
} from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import type { NotificationItem, NotificationKind } from '../hooks/useNotificationSocket'

/**
 * 站内新消息弹窗（右下角浮层）
 *
 * 和系统通知（浏览器通知 + 标题闪烁）分工不同：
 * 系统通知管"标签页在后台"；这里管"人就看着主站"，弹一下能点进去。
 * 数据与过滤在 useNotificationSocket，这里只负责文案与排版。
 */

/** 自动收起时间（悬停时暂停：人正看着别抢走） */
const AUTO_DISMISS_MS = 6000

type Tone = 'message' | 'success' | 'error' | 'system' | 'mention'

const KIND_STYLE: Record<NotificationKind, { icon: typeof Bell; tone: Tone }> = {
  group_message: { icon: Users, tone: 'message' },
  dm_message: { icon: MessageSquare, tone: 'message' },
  announcement: { icon: Megaphone, tone: 'system' },
  group_invite_card: { icon: Mail, tone: 'message' },
  friend_request: { icon: UserPlus, tone: 'message' },
  friend_accepted: { icon: CheckCircle2, tone: 'success' },
  friend_rejected: { icon: XCircle, tone: 'error' },
  group_join_requested: { icon: UserPlus, tone: 'message' },
  group_join_approved: { icon: CheckCircle2, tone: 'success' },
  group_join_rejected: { icon: XCircle, tone: 'error' },
  group_invite_approved: { icon: CheckCircle2, tone: 'success' },
  group_invite_denied: { icon: XCircle, tone: 'error' },
  group_invite_accepted: { icon: CheckCircle2, tone: 'success' },
  group_invite_declined: { icon: XCircle, tone: 'error' },
  system: { icon: Bell, tone: 'system' },
}

const TONE_CLASS: Record<Tone, string> = {
  message: 'text-primary-400',
  success: 'text-mint-400',
  error: 'text-rose-400',
  system: 'text-accent-400',
  // 被点名的（个人点名或 @all）用会话列表同款"未读红"：一眼分出"值得马上看"和"路过一条消息"
  mention: 'text-rose-400',
}

/** 用 ' · ' 拼非空片段：免得出现「· 群名」这种开头孤零零的分隔符 */
function joinParts(...parts: (string | null | undefined)[]): string {
  // 只认字符串：早先用 !!p 判空，将来有人塞数字进来，0 会被静默丢掉
  return parts.filter((p): p is string => typeof p === 'string' && p.trim() !== '').join(' · ')
}

/**
 * 被点名时标题前挂一句说明，其余照旧
 *
 * 个人点名优先于 @all：两个都为真说明这条既 @ 了全体又点到你，说"有人 @ 你"更准；
 * 反过来（@all 却说"你"）是撒谎。
 */
function withMention(item: NotificationItem, title: string, t: (k: string) => string): string {
  const label = item.mentionedMe
    ? t('notify.mentionedYou')
    : (item.mentionedAll ? t('notify.mentionedAll') : '')
  return label ? (joinParts(label, title) || title) : title
}

function describe(item: NotificationItem, t: (k: string) => string): { title: string; body: string } {
  const { kind, place, sender, preview } = item
  switch (kind) {
    case 'group_message':
      return { title: withMention(item, place || t('notify.groupMessage'), t), body: joinParts(sender, preview) }
    case 'dm_message':
      return { title: withMention(item, place || t('notify.dmMessage'), t), body: preview || '' }
    case 'announcement':
      return {
        // 目前公告都是经 push 以 group_message 落下来的（见 connection_manager._push_to_scopes），
        // 走不到这个分支；真收到裸 announcement 帧时也照挂点名说明，别把"这事和你有关"吞掉
        title: withMention(item, joinParts(place, t('notify.announcement')) || t('notify.announcement'), t),
        body: preview || '',
      }
    case 'group_invite_card':
      return { title: t('notify.groupInviteCard'), body: joinParts(place, preview) }
    case 'friend_request':
      return { title: t('notify.friendRequest'), body: joinParts(sender, preview) }
    case 'friend_accepted':
      return { title: t('notify.friendAccepted'), body: sender || '' }
    case 'friend_rejected':
      return { title: t('notify.friendRejected'), body: sender || '' }
    case 'group_join_requested':
      return { title: t('notify.groupJoinRequested'), body: joinParts(sender, place) }
    case 'group_join_approved':
      return { title: t('notify.groupJoinApproved'), body: place || '' }
    case 'group_join_rejected':
      return { title: t('notify.groupJoinRejected'), body: place || '' }
    case 'group_invite_approved':
      return { title: t('notify.groupInviteApproved'), body: joinParts(sender, place) }
    case 'group_invite_denied':
      return { title: t('notify.groupInviteDenied'), body: joinParts(sender, place) }
    case 'group_invite_accepted':
      return { title: t('notify.groupInviteAccepted'), body: joinParts(sender, place) }
    case 'group_invite_declined':
      return { title: t('notify.groupInviteDeclined'), body: joinParts(sender, place) }
    case 'system':
      return { title: t('notify.system'), body: preview || '' }
    default: {
      // 新增 kind 忘了加分支时这里编译不过（KIND_STYLE 那张表靠 Record 守住，这张靠 never 守）；
      // 真到了运行时（新后端 + 老前端）给个兜底标题，别白屏
      const _exhaustive: never = kind
      return { title: t('notify.system'), body: preview || '' }
    }
  }
}

function Toast({ item, onDismiss, onOpen }: {
  item: NotificationItem
  onDismiss: (id: string) => void
  onOpen: (item: NotificationItem) => void
}) {
  const t = useT()
  const [hovering, setHovering] = useState(false)
  const { title, body } = describe(item, t)
  // 计时器与回调身份无关，所以 onDismiss 走 ref：父组件每次渲染都给新函数，
  // 直接进依赖会让 6 秒计时器被反复重置——症状就是"浮窗永远不消失"
  const dismissRef = useRef(onDismiss)
  dismissRef.current = onDismiss

  const { icon, tone } = KIND_STYLE[item.kind]
  // 被点名的换图标与颜色：和普通群消息长得一样，等于没做特殊处理
  //（个人点名与 @all 都算点名；破免打扰的只有前者，判定在 useNotificationSocket 里）
  const highlighted = item.mentionedMe || item.mentionedAll
  const Icon = highlighted ? AtSign : icon
  const cardTone = highlighted ? 'mention' : tone

  useEffect(() => {
    // 被点名的不自动收起：等人自己看（其余照旧 6 秒，免得回到电脑前积一屏）
    if (hovering || highlighted) return
    const timer = setTimeout(() => dismissRef.current(item.id), AUTO_DISMISS_MS)
    return () => clearTimeout(timer)
  }, [hovering, item.id, highlighted])

  return (
    <div
      onClick={() => onOpen(item)}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      className={`pointer-events-auto flex items-start gap-3 p-3 bg-elevated border rounded-dialog shadow-2xl shadow-black/30 animate-slide-in cursor-pointer ${highlighted ? 'border-rose-400/40' : 'border-border'} ${item.to ? 'hover:border-primary-400/40' : ''}`}
    >
      {item.avatarUrl ? (
        <img src={item.avatarUrl} alt="" className="w-8 h-8 rounded-full object-cover shrink-0" loading="lazy" />
      ) : (
        <span className={`w-8 h-8 rounded-control bg-canvas flex items-center justify-center shrink-0 ${TONE_CLASS[cardTone]}`}>
          <Icon size={15} />
        </span>
      )}

      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-textPrimary truncate">{title}</div>
        {body && <div className="text-xs text-textMuted line-clamp-2 mt-0.5 break-words">{body}</div>}
      </div>

      <button
        onClick={(e) => { e.stopPropagation(); onDismiss(item.id) }}
        className="p-0.5 rounded text-textMuted hover:text-textSecondary shrink-0"
        title={t('notify.close')}
      >
        <X size={12} />
      </button>
    </div>
  )
}

export default function NotificationToasts({ items, onDismiss }: {
  items: NotificationItem[]
  onDismiss: (id: string) => void
}) {
  const navigate = useNavigate()
  if (items.length === 0) return null

  return (
    // 移动端抬到底部导航之上；pointer-events 只给卡片，别挡住下面的页面
    <div className="fixed bottom-20 md:bottom-4 right-4 z-toast flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)] pointer-events-none">
      {items.map((item) => (
        <Toast
          key={item.id}
          item={item}
          onDismiss={onDismiss}
          onOpen={(n) => {
            if (n.to) navigate(n.to)
            onDismiss(n.id)
          }}
        />
      ))}
    </div>
  )
}
