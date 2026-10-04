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
