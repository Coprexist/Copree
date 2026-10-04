/**
 * filePreview 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('filePreview:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const filePreviewZh: TranslationDict = {

  // ======================== 文件预览 / FilePreview ========================
  'fileTooLarge': '… 文件过大，仅显示前 2MB',
  'viewRendered': '渲染',
  'viewSource': '原文',
  'viewRenderedHint': '查看渲染效果',
  'viewSourceHint': '查看原文源码',
}

export const filePreviewEn: TranslationDict = {
  'fileTooLarge': '… File too large, showing first 2MB',
  'viewRendered': 'Rendered',
  'viewSource': 'Source',
  'viewRenderedHint': 'View rendered',
  'viewSourceHint': 'View source',
}

export const filePreviewJa: TranslationDict = {
  'fileTooLarge': '… ファイルが大きすぎるため、最初の2MBのみ表示',
  'viewRendered': 'レンダリング',
  'viewSource': '原文',
  'viewRenderedHint': 'レンダリング表示に切り替え',
  'viewSourceHint': '原文を表示',
}
