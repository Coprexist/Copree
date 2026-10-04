/**
 * search 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('search:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const searchZh: TranslationDict = {

  // ======================== 搜索 / SearchOverlay ========================
  'placeholder': '搜索用户、AI 或群聊...',
  'searching': '搜索中...',
  'noResults': '无结果',
  'creator': '创建者:',
  'sendDM': '发私信',
  'sendDMFailed': '发起私信失败',
  'addFriend': '加好友',
  'addFriendSuccess': '好友申请已发送',
  'addFriendFailed': '发送好友申请失败',
  'sectionPeople': '用户与 AI',
  'sectionGroups': '群聊',
  'members': '人',
  'enterGroup': '进入',
  'joinGroup': '加入',
  'requestJoin': '申请加入',
  'requested': '已申请',
  'joinRequested': '入群申请已提交，等待群主或管理员审批',
  'joinFailed': '申请入群失败',
}

export const searchEn: TranslationDict = {
  'placeholder': 'Search users, AI or groups...',
  'searching': 'Searching...',
  'noResults': 'No results',
  'creator': 'Creator:',
  'sendDM': 'Send DM',
  'sendDMFailed': 'Failed to start DM',
  'addFriend': 'Add Friend',
  'addFriendSuccess': 'Friend request sent',
  'addFriendFailed': 'Failed to send friend request',
  'sectionPeople': 'People and AI',
  'sectionGroups': 'Groups',
  'members': 'members',
  'enterGroup': 'Open',
  'joinGroup': 'Join',
  'requestJoin': 'Request',
  'requested': 'Requested',
  'joinRequested': 'Join request submitted — waiting for an owner or admin',
  'joinFailed': 'Failed to join the group',
}

export const searchJa: TranslationDict = {
  'placeholder': 'ユーザー・AI・グループを検索...',
  'searching': '検索中...',
  'noResults': '結果なし',
  'creator': '作成者:',
  'sendDM': 'DMを送信',
  'sendDMFailed': 'DMの開始に失敗しました',
  'addFriend': 'フレンド追加',
  'addFriendSuccess': 'フレンド申請を送信しました',
  'addFriendFailed': 'フレンド申請の送信に失敗しました',
  'sectionPeople': 'ユーザーとAI',
  'sectionGroups': 'グループ',
  'members': '人',
  'enterGroup': '開く',
  'joinGroup': '参加',
  'requestJoin': '参加申請',
  'requested': '申請済み',
  'joinRequested': '参加申請を送信しました。オーナーまたは管理者の承認をお待ちください',
  'joinFailed': 'グループへの参加に失敗しました',
}
