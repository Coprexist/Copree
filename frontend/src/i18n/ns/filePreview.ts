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
  // SVG 的下载格式选择（预览弹窗）
  'downloadFormat': '下载格式',
  'downloadAsSvg': 'SVG 原图（矢量）',
  'downloadAsPng': 'PNG 图片（尺寸可选）…',
  'exportPngTitle': '导出 PNG',
  'exportPngNatural': '原图 {w}×{h}',
  'exportPngOriginal': '原尺寸',
  'exportPngLongestSide': '长边像素',
  'exportPngOutput': '输出 {w}×{h}',
  'exportPngSave': '导出',
  'exportStripBg': '去掉 SVG 自带的满幅底色（{fill}）',
  'exportStripBgWarn': '不推荐',
  'exportStripBgHint': '这层底色往往就是图标本身（比如深色圆角底）。勾上它会整块消失、只剩上面的图形；只有确定要一张没有底的裸图时才勾。',
  'exportPngClientHint': '发给 QQ / 微信这类客户端时建议不超过 1024：超大 PNG 在对方预览里可能先按黑底合成，发出后才恢复透明。',
  'exportPngFailed': 'PNG 导出失败，试试 SVG 原图',
  // 收进我的文件（§7.3）：一份硬链接，占我的 100 MB、平台不再自动删
  'keep': '收进我的文件',
  'kept': '已在我的文件里',
  'keepFailed': '没能收进我的文件',
}

export const filePreviewEn: TranslationDict = {
  'fileTooLarge': '… File too large, showing first 2MB',
  'viewRendered': 'Rendered',
  'viewSource': 'Source',
  'viewRenderedHint': 'View rendered',
  'viewSourceHint': 'View source',
  'downloadFormat': 'Download format',
  'downloadAsSvg': 'SVG original (vector)',
  'downloadAsPng': 'PNG image (pick size)…',
  'exportPngTitle': 'Export PNG',
  'exportPngNatural': 'Original {w}×{h}',
  'exportPngOriginal': 'Original',
  'exportPngLongestSide': 'Longest side (px)',
  'exportPngOutput': 'Output {w}×{h}',
  'exportPngSave': 'Export',
  'exportStripBg': 'Remove the full-canvas fill inside the SVG ({fill})',
  'exportStripBgWarn': 'not recommended',
  'exportStripBgHint': 'That fill is usually the icon itself (e.g. the dark rounded base). Ticking deletes that layer entirely; only tick if you want a bare graphic on transparency.',
  'exportPngClientHint': 'For QQ / WeChat and the like, stay at or below 1024: very large PNGs may preview on a black matte before the sent copy shows the alpha.',
  'exportPngFailed': 'PNG export failed — try the SVG original',
  'keep': 'Keep in my files',
  'kept': 'In my files',
  'keepFailed': 'Could not keep in my files',
}

export const filePreviewJa: TranslationDict = {
  'fileTooLarge': '… ファイルが大きすぎるため、最初の2MBのみ表示',
  'viewRendered': 'レンダリング',
  'viewSource': '原文',
  'viewRenderedHint': 'レンダリング表示に切り替え',
  'viewSourceHint': '原文を表示',
  'downloadFormat': 'ダウンロード形式',
  'downloadAsSvg': 'SVG 原本（ベクター）',
  'downloadAsPng': 'PNG 画像（サイズ指定）…',
  'exportPngTitle': 'PNG を書き出す',
  'exportPngNatural': '原本 {w}×{h}',
  'exportPngOriginal': '原本サイズ',
  'exportPngLongestSide': '長辺のピクセル数',
  'exportPngOutput': '出力 {w}×{h}',
  'exportPngSave': '書き出す',
  'exportStripBg': 'SVG 内の全面の塗りを除去（{fill}）',
  'exportStripBgWarn': '非推奨',
  'exportStripBgHint': 'この塗りは多くの場合アイコン本体（暗い角丸の下地など）です。チェックすると丸ごと消えます。透明な素の図形が欲しいときだけチェックしてください。',
  'exportPngClientHint': 'QQ / WeChat などへ送るなら 1024 以下が無難です。巨大な PNG は相手側のプレビューで黒地に合成され、送信後に透明へ戻ることがあります。',
  'exportPngFailed': 'PNG の書き出しに失敗しました。SVG 原本をお試しください',
  'keep': 'マイファイルに保存',
  'kept': 'マイファイルに保存済み',
  'keepFailed': 'マイファイルに保存できませんでした',
}
