/**
 * aiType 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('aiType:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const aiTypeZh: TranslationDict = {

  // ======================== AI 类型 / AI Type ========================
  'resonance': '共振',
  'general': '通用',
  'semiGeneral': '半通用',
}

export const aiTypeEn: TranslationDict = {
  'resonance': 'Resonance',
  'general': 'General',
  'semiGeneral': 'Semi-General',
}

export const aiTypeJa: TranslationDict = {
  'resonance': '共振',
  'general': '汎用',
  'semiGeneral': '準汎用',
}
