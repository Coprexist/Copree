/**
 * dmSettings 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('dmSettings:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const dmSettingsZh: TranslationDict = {

  // ======================== 私信设置 / DMSettings ========================
  'title': '私信设置',
  'remark': '备注',
  'remarkPlaceholder': '给这个人起个称呼（只对你可见）',
  'remarkHint': '会话列表优先显示备注，而不是对方的原名。',
  'remarkSaveFailed': '备注保存失败',
  'dnd': '消息免打扰',
  'dndEnabled': '免打扰已开启',
  'cancelDnd': '取消免打扰',
  'dndHint': '选择免打扰时长，期间不会收到私信通知',
  'dnd15min': '15 分钟',
  'dnd30min': '30 分钟',
  'dnd1hour': '1 小时',
  'dnd4hours': '4 小时',
  'dnd8hours': '8 小时',
  'dndForever': '永久',
  'exportChat': '导出聊天记录',
  'downloadExport': '下载导出文件',
}

export const dmSettingsEn: TranslationDict = {
  'title': 'DM Settings',
  'remark': 'Remark',
  'remarkPlaceholder': 'A name only you will see',
  'remarkHint': 'The conversation list shows this remark instead of the original name.',
  'remarkSaveFailed': 'Failed to save the remark',
  'dnd': 'Message Mute / DND',
  'dndEnabled': 'DND is on',
  'cancelDnd': 'Cancel DND',
  'dndHint': "Select DND duration; you won't receive DM notifications",
  'dnd15min': '15 min',
  'dnd30min': '30 min',
  'dnd1hour': '1 hour',
  'dnd4hours': '4 hours',
  'dnd8hours': '8 hours',
  'dndForever': 'Forever',
  'exportChat': 'Export Chat History',
  'downloadExport': 'Download Export',
}

export const dmSettingsJa: TranslationDict = {
  'title': 'DM設定',
  'remark': 'メモ',
  'remarkPlaceholder': '自分だけに見える呼び名をつける',
  'remarkHint': '会話一覧では元の名前よりこのメモを優先して表示します。',
  'remarkSaveFailed': 'メモを保存できませんでした',
  'dnd': 'メッセージおやすみ',
  'dndEnabled': 'おやすみモード中',
  'cancelDnd': 'おやすみ解除',
  'dndHint': 'おやすみ時間を選択すると、その間DM通知を受け取らなくなります',
  'dnd15min': '15分',
  'dnd30min': '30分',
  'dnd1hour': '1時間',
  'dnd4hours': '4時間',
  'dnd8hours': '8時間',
  'dndForever': '永続',
  'exportChat': 'チャット履歴をエクスポート',
  'downloadExport': 'エクスポートをダウンロード',
}
