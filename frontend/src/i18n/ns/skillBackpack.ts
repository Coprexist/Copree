/**
 * skillBackpack 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('skillBackpack:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const skillBackpackZh: TranslationDict = {

  // ── 技能背包 ──
  'title': '技能背包',
  'tools': '个工具',
  'triggerConditions': '触发条件',
  'availableIn': '当前状态可用',
  'unavailableIn': '当前状态不可用',
  'adminDesc': '管理说明',
  'aiDesc': 'AI 视角',
}

export const skillBackpackEn: TranslationDict = {
  'title': 'Skill Backpack',
  'tools': 'tools',
  'triggerConditions': 'Trigger Conditions',
  'availableIn': 'Available Now',
  'unavailableIn': 'Unavailable Now',
  'adminDesc': 'Admin Description',
  'aiDesc': 'AI Perspective',
}

export const skillBackpackJa: TranslationDict = {
  'title': 'スキルバックパック',
  'tools': '個のツール',
  'triggerConditions': 'トリガー条件',
  'availableIn': '現在利用可能',
  'unavailableIn': '現在利用不可',
  'adminDesc': '管理者向け説明',
  'aiDesc': 'AI視点',
}
