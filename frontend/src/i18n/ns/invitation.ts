/**
 * invitation 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('invitation:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const invitationZh: TranslationDict = {

  // 群邀请卡片
  'title': '{inviter} 邀请你加入群聊',
  'groupLabel': '群聊',
  'accept': '接受',
  'reject': '拒绝',
  'accepted': '已接受',
  'rejected': '已拒绝',
}

export const invitationEn: TranslationDict = {
  'title': '{inviter} invited you to a group',
  'groupLabel': 'Group',
  'accept': 'Accept',
  'reject': 'Reject',
  'accepted': 'Accepted',
  'rejected': 'Rejected',
}

export const invitationJa: TranslationDict = {
  'title': '{inviter}がグループに招待しました',
  'groupLabel': 'グループ',
  'accept': '参加',
  'reject': '拒否',
  'accepted': '参加済み',
  'rejected': '拒否しました',
}
