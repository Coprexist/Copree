/**
 * sidebar 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('sidebar:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const sidebarZh: TranslationDict = {

  // ======================== 侧边栏 / Sidebar ========================
  'chat': '聊天',
  'list': '列表',
  'myAi': '我的 AI',
  'settings': '我的',
  'admin': '管理',
  'manual': '手册',
  'logout': '退出',
  'tools': '小工具',
  'studyRoom': '自习室',
  'expand': '展开',
  'collapse': '折叠',
  'adminPanel': '管理员',
  'usageManual': '使用手册',
  'quota': '额度',
  'balance': '余额',
  'expiredSession': '会话已失效，请重新登录',
}

export const sidebarEn: TranslationDict = {
  'chat': 'Chat',
  'list': 'List',
  'myAi': 'My AI',
  'settings': 'Me',
  'admin': 'Admin',
  'manual': 'Guide',
  'logout': 'Logout',
  'tools': 'Tools',
  'studyRoom': 'Study Room',
  'expand': 'Expand',
  'collapse': 'Collapse',
  'adminPanel': 'Admin Panel',
  'usageManual': 'User Guide',
  'quota': 'Quota',
  'balance': 'Balance',
  'expiredSession': 'Session expired, please login again',
}

export const sidebarJa: TranslationDict = {
  'chat': 'チャット',
  'list': 'リスト',
  'myAi': 'マイ AI',
  'settings': 'マイ',
  'admin': '管理',
  'manual': 'ガイド',
  'logout': 'ログアウト',
  'tools': 'ツール',
  'studyRoom': '自習室',
  'expand': '展開',
  'collapse': '折りたたむ',
  'adminPanel': '管理パネル',
  'usageManual': '利用ガイド',
  'quota': 'クォータ',
  'balance': '残高',
  'expiredSession': 'セッションの有効期限が切れました。再度ログインしてください',
}
