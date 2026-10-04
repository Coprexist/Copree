/**
 * time 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('time:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const timeZh: TranslationDict = {

  // ======================== 时间 / Time ========================
  'yesterday': '昨天',
  'daysAgo': '天前',
  'weeksAgo': '周前',
  'justNow': '刚刚',
}

export const timeEn: TranslationDict = {
  'yesterday': 'Yesterday',
  'daysAgo': ' days ago',
  'weeksAgo': ' weeks ago',
  'justNow': 'Just now',
}

export const timeJa: TranslationDict = {
  'yesterday': '昨日',
  'daysAgo': '日前',
  'weeksAgo': '週間前',
  'justNow': 'たった今',
}
