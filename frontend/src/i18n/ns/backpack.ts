/**
 * backpack 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('backpack:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const backpackZh: TranslationDict = {

  // ======================== 技能背包 / Skill Backpack ========================
  'title': '技能背包',
  'toolCount': '个工具',
  'triggerConditions': '触发条件',
  'toolsInSkill': '技能内工具',
  'aiDesc': 'AI 描述',
  'adminDesc': '管理说明',
  'availableNow': '当前可用',
  'unavailableNow': '当前不可用',
  'currentState': '当前状态',
  'expandDetails': '展开详情',
  'moreTriggers': '个触发条件',
  'desc': 'AI 拥有的技能（工具），按技能段分类展示。展开可查看每个工具的管理说明和触发条件。',
}

export const backpackEn: TranslationDict = {
  'title': 'Skill Backpack',
  'toolCount': 'tools',
  'triggerConditions': 'Trigger Conditions',
  'toolsInSkill': 'Tools in Skill',
  'aiDesc': 'AI Description',
  'adminDesc': 'Admin Description',
  'availableNow': 'Available Now',
  'unavailableNow': 'Unavailable',
  'currentState': 'Current State',
  'expandDetails': 'Expand Details',
  'moreTriggers': 'more triggers',
  'desc': 'Skills (tools) available to the AI, organized by category. Expand to view each tool\'s admin description and trigger conditions.',
}

export const backpackJa: TranslationDict = {
  'title': 'スキルバックパック',
  'toolCount': '個のツール',
  'triggerConditions': 'トリガー条件',
  'toolsInSkill': 'スキル内ツール',
  'aiDesc': 'AIの説明',
  'adminDesc': '管理者の説明',
  'availableNow': '現在利用可能',
  'unavailableNow': '現在利用不可',
  'currentState': '現在の状態',
  'expandDetails': '詳細を展開',
  'moreTriggers': '個のトリガー条件',
  'desc': 'AIが利用可能なスキル（ツール）をカテゴリ別に表示します。展開すると各ツールの管理者向け説明とトリガー条件を確認できます。',
}
