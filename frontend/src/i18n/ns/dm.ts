/**
 * dm 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('dm:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const dmZh: TranslationDict = {

  // ======================== 私信 / DM ========================
  'online': '在线',
  'dnd': '免打扰',
  'offline': '离线',
  'lastActive': '最近在线',
  'shortDnd': '勿扰',
  'sessionList': '会话列表',
  'loading': '加载中...',
  'unmute': '取消免打扰',
  'mute': '开启免打扰',
  'dmSettings': '私信设置',
  'user': '用户',
  'ai': 'AI',
}

export const dmEn: TranslationDict = {
  'online': 'Online',
  'dnd': 'Do Not Disturb',
  'offline': 'Offline',
  'lastActive': 'Last seen',
  'shortDnd': 'DND',
  'sessionList': 'Session List',
  'loading': 'Loading...',
  'unmute': 'Unmute',
  'mute': 'Mute',
  'dmSettings': 'DM Settings',
  'user': 'User',
  'ai': 'AI',
}

export const dmJa: TranslationDict = {
  'online': 'オンライン',
  'dnd': 'おやすみモード',
  'offline': 'オフライン',
  'lastActive': '最終アクティブ',
  'shortDnd': 'おやすみ',
  'sessionList': 'セッション一覧',
  'loading': '読み込み中...',
  'unmute': 'おやすみ解除',
  'mute': 'おやすみ',
  'dmSettings': 'DM設定',
  'user': 'ユーザー',
  'ai': 'AI',
}
