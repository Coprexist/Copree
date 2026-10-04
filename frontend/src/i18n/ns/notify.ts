/**
 * notify 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('notify:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const notifyZh: TranslationDict = {

  // ======================== 站内通知 / In-app popups ========================
  'groupMessage': '新消息',
  'mentionedYou': '有人 @ 你',
  'mentionedAll': '@ 全体成员',
  'dmMessage': '新私信',
  'announcement': '群公告',
  'groupInviteCard': '群聊邀请',
  'friendRequest': '好友申请',
  'friendAccepted': '好友申请已通过',
  'friendRejected': '好友申请被拒绝',
  'groupJoinRequested': '入群申请',
  'groupJoinApproved': '入群申请已通过',
  'groupJoinRejected': '入群申请被拒绝',
  'groupInviteApproved': '邀请已获批',
  'groupInviteDenied': '邀请被驳回',
  'groupInviteAccepted': '对方已接受邀请',
  'groupInviteDeclined': '对方拒绝了邀请',
  'system': '系统通知',
  'close': '关闭',
}

export const notifyEn: TranslationDict = {
  'groupMessage': 'New message',
  'mentionedYou': 'You were mentioned',
  'mentionedAll': '@everyone',
  'dmMessage': 'New DM',
  'announcement': 'Announcement',
  'groupInviteCard': 'Group invitation',
  'friendRequest': 'Friend request',
  'friendAccepted': 'Friend request accepted',
  'friendRejected': 'Friend request rejected',
  'groupJoinRequested': 'Join request',
  'groupJoinApproved': 'Join request approved',
  'groupJoinRejected': 'Join request rejected',
  'groupInviteApproved': 'Invite approved',
  'groupInviteDenied': 'Invite denied',
  'groupInviteAccepted': 'Invite accepted',
  'groupInviteDeclined': 'Invite declined',
  'system': 'System notice',
  'close': 'Close',
}

export const notifyJa: TranslationDict = {
  'groupMessage': '新着メッセージ',
  'mentionedYou': 'あなたがメンションされました',
  'mentionedAll': '@全体メンバー',
  'dmMessage': '新着DM',
  'announcement': 'お知らせ',
  'groupInviteCard': 'グループ招待',
  'friendRequest': 'フレンド申請',
  'friendAccepted': 'フレンド申請が承認されました',
  'friendRejected': 'フレンド申請が拒否されました',
  'groupJoinRequested': '参加申請',
  'groupJoinApproved': '参加申請が承認されました',
  'groupJoinRejected': '参加申請が拒否されました',
  'groupInviteApproved': '招待が承認されました',
  'groupInviteDenied': '招待が却下されました',
  'groupInviteAccepted': '相手が招待を承諾しました',
  'groupInviteDeclined': '相手が招待を拒否しました',
  'system': 'システム通知',
  'close': '閉じる',
}
