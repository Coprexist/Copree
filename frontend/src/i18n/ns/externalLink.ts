/**
 * externalLink 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('externalLink:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const externalLinkZh: TranslationDict = {

  // ======================== 外部链接安全弹窗 ========================
  'title': '即将离开本站',
  'hint': '你即将前往以下外部网页：',
  'warning': '我们无法保证外部网站的安全性，请谨慎操作。',
  'confirm': '确认前往',
}

export const externalLinkEn: TranslationDict = {
  'title': 'Leaving this site',
  'hint': 'You are about to visit the following external website:',
  'warning': 'We cannot guarantee the security of external websites. Please proceed with caution.',
  'confirm': 'Proceed',
}

export const externalLinkJa: TranslationDict = {
  'title': 'サイトを離れます',
  'hint': '次の外部ページへ移動しようとしています：',
  'warning': '外部サイトの安全性は保証できません。十分ご注意ください。',
  'confirm': '移動する',
}
