/**
 * nav 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('nav:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const navZh: TranslationDict = {

  // ======================== 导航 / Navigation ========================
  'chat': '聊天',
  'worlds': '群视界',
  'market': '世界商城',
  'list': '列表',
  'ai': 'AI',
  'me': '我的',
  'settings': '设置',
  'admin': '管理',
  'manual': '使用手册',
  'adminManual': '管理手册',
}

export const navEn: TranslationDict = {
  'chat': 'Chat',
  'worlds': 'Worlds',
  'market': 'World Market',
  'list': 'List',
  'ai': 'AI',
  'me': 'Me',
  'settings': 'Settings',
  'admin': 'Admin',
  'manual': 'Manual',
  'adminManual': 'Admin Manual',
}

export const navJa: TranslationDict = {
  'chat': 'チャット',
  'worlds': 'ワールド',
  'market': 'マーケット',
  'list': 'リスト',
  'ai': 'AI',
  'me': 'マイ',
  'settings': '設定',
  'admin': '管理',
  'manual': 'マニュアル',
  'adminManual': '管理マニュアル',
}
