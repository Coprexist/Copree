/**
 * avatarCrop 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('avatarCrop:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const avatarCropZh: TranslationDict = {

  // ======================== 头像裁剪 / AvatarCrop ========================
  'adjustAvatar': '调整头像',
}

export const avatarCropEn: TranslationDict = {
  'adjustAvatar': 'Adjust Avatar',
}

export const avatarCropJa: TranslationDict = {
  'adjustAvatar': 'アバターを調整',
}
