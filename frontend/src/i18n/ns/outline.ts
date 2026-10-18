/**
 * outline 命名空间字典（i18n 分区）
 *
 * 长文档页右侧的「本页目录」（书签侧栏）：搜索小节、回到开头 / 跳到结尾、复制锚点。
 * 调用点写 t('outline:key')。
 */

import type { TranslationDict } from '../types'

export const outlineZh: TranslationDict = {
  'title': '本页目录',
  'search': '搜索小节…',
  'noMatch': '没有匹配的小节',
  'top': '回到开头',
  'bottom': '跳到结尾',
  'open': '目录',
  'copyLink': '复制这一节的链接',
  'copied': '已复制',
  'progress': '已读',
  'logTitle': '正文书签',
  'searchBody': '搜索正文…',
}

export const outlineEn: TranslationDict = {
  'title': 'On this page',
  'search': 'Search sections…',
  'noMatch': 'No matching section',
  'top': 'Back to top',
  'bottom': 'Jump to end',
  'open': 'Contents',
  'copyLink': 'Copy link to this section',
  'copied': 'Copied',
  'progress': 'Read',
  'logTitle': 'Body bookmarks',
  'searchBody': 'Search the body…',
}

export const outlineJa: TranslationDict = {
  'title': 'このページ',
  'search': 'セクションを検索…',
  'noMatch': '一致するセクションがありません',
  'top': '先頭へ',
  'bottom': '末尾へ',
  'open': '目次',
  'copyLink': 'このセクションのリンクをコピー',
  'copied': 'コピーしました',
  'progress': '既読',
  'logTitle': '本文のブックマーク',
  'searchBody': '本文を検索…',
}
