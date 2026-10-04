/**
 * worldChat 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('worldChat:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const worldChatZh: TranslationDict = {
  'expressionMode': '群视界对话的表达方式',
  'expressionModeDesc': '世界里的 AI 怎么跟你说话：术语看不懂就选通俗模式，随时能换回来',
  'expressionModePro': '专业模式',
  'expressionModeProDesc': '读者懂行：术语直接用，结论和代码优先，不做基础科普',
  'expressionModePlain': '通俗模式',
  'expressionModePlainDesc': '少用术语：能不用就不用，出现时带一句大白话解释',
}

export const worldChatEn: TranslationDict = {
  'expressionMode': 'Expression style in world chats',
  'expressionModeDesc': 'How the AI talks inside a world. Pick Plain language if technical terms slow you down; switch back anytime',
  'expressionModePro': 'Professional',
  'expressionModeProDesc': 'For readers who know the field: terms as-is, conclusions and code first, no basic explanations',
  'expressionModePlain': 'Plain language',
  'expressionModePlainDesc': 'Fewer technical terms: avoided when possible, explained when used',
}

export const worldChatJa: TranslationDict = {
  'expressionMode': 'ワールド会話での表現スタイル',
  'expressionModeDesc': 'ワールド内で AI が話すときの表現です。専門用語が分かりにくいときは「やさしい表現」を。いつでも戻せます',
  'expressionModePro': 'プロフェッショナル',
  'expressionModeProDesc': '知識のある読者向け：用語はそのまま、結論とコードを優先し、基本の説明は省きます',
  'expressionModePlain': 'やさしい表現',
  'expressionModePlainDesc': '専門用語はできるだけ使わず、使うときはやさしい説明をつけます',
}
