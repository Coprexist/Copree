/**
 * notFound 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('notFound:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const notFoundZh: TranslationDict = {
  'title': '页面不存在',
  'desc': '你找的页面可能已被移除、改名，或暂时不可用。',
  'backHome': '返回首页',
}

export const notFoundEn: TranslationDict = {
  'title': 'Page Not Found',
  'desc': 'The page you are looking for might have been removed, renamed, or is temporarily unavailable.',
  'backHome': 'Back to Home',
}

export const notFoundJa: TranslationDict = {
  'title': 'ページが見つかりません',
  'desc': 'お探しのページは削除されたか、名前が変更されたか、一時的に利用できません。',
  'backHome': 'ホームに戻る',
}
