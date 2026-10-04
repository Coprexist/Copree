/**
 * forward 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('forward:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const forwardZh: TranslationDict = {

  // ======================== 文件转发 / Forward ========================
  'searchPlaceholder': '搜索群聊或联系人...',
  'noTargets': '暂无群聊或联系人',
  'groupsLabel': '群聊',
  'dmLabel': '联系人',
  'selectedCount': '已选 {n} 个',
  'selectHint': '请选择转发目标',
  'send': '发送',
  'sent': '已发送',
  'title': '转发文件',
  'release': '释放引用',
  'forwarded': '转发',
}

export const forwardEn: TranslationDict = {
  'searchPlaceholder': 'Search groups or contacts...',
  'noTargets': 'No groups or contacts',
  'groupsLabel': 'Groups',
  'dmLabel': 'Contacts',
  'selectedCount': '{n} selected',
  'selectHint': 'Select targets',
  'send': 'Send',
  'sent': 'Sent',
  'title': 'Forward File',
  'release': 'Release',
  'forwarded': 'Forwarded',
}

export const forwardJa: TranslationDict = {
  'searchPlaceholder': 'グループまたは連絡先を検索...',
  'noTargets': 'グループまたは連絡先がありません',
  'groupsLabel': 'グループ',
  'dmLabel': '連絡先',
  'selectedCount': '{n} 件選択中',
  'selectHint': '転送先を選択してください',
  'send': '送信',
  'sent': '送信済み',
  'title': 'ファイル転送',
  'release': '参照を解除',
  'forwarded': '転送済み',
}
