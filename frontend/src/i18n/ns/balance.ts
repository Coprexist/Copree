/**
 * balance 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('balance:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const balanceZh: TranslationDict = {

  // v0.1.8 余额弹窗
  'title': '额度不足',
  'useOwnKeyPrompt': '你的额度不足，是否使用自有 API Key 继续与「{name}」对话？',
  'useOwnKey': '使用自有 Key',
  'cancel': '取消',
}

export const balanceEn: TranslationDict = {
  'title': 'Insufficient Balance',
  'useOwnKeyPrompt': 'Your balance is insufficient. Use your own API Key to continue chatting with "{name}"?',
  'useOwnKey': 'Use Own Key',
  'cancel': 'Cancel',
}

export const balanceJa: TranslationDict = {
  'title': '残高不足',
  'useOwnKeyPrompt': '残高が不足しています。独自のAPI Keyを使って「{name}」との会話を続けますか？',
  'useOwnKey': '独自Keyを使用',
  'cancel': 'キャンセル',
}
