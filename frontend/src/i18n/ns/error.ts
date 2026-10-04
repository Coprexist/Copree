/**
 * error 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('error:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const errorZh: TranslationDict = {

  // ======================== 错误信息 / Errors ========================
  'unauthorized': '未授权',
  'requestFailed': '请求失败 ({status})',
  'uploadFailed': '上传失败 ({status})',
  'unknown': '未知错误',
  'saveFailed': '保存失败',
  'createAgentFailed': '创建失败',
  'startDmFailed': '发起私信失败',
  'operationFailed': '操作失败',
  'invalidMinutes': '请输入有效的分钟数',
  'dndMaxDuration': '单次免打扰最长 7 天（10080 分钟）',
  'leaveFailed': '退出失败',
  'exportFailed': '导出失败',
  'cancelFailed': '取消失败',
  'dndSetFailed': '设置失败',
  'testFailed': '测试失败',
  'avatarTooLarge': '头像不能超过 2MB',
}

export const errorEn: TranslationDict = {
  'unauthorized': 'Unauthorized',
  'requestFailed': 'Request failed ({status})',
  'uploadFailed': 'Upload failed ({status})',
  'unknown': 'Unknown error',
  'saveFailed': 'Save failed',
  'createAgentFailed': 'Creation failed',
  'startDmFailed': 'Failed to start DM',
  'operationFailed': 'Operation failed',
  'invalidMinutes': 'Please enter a valid number of minutes',
  'dndMaxDuration': 'DND max duration is 7 days (10080 minutes)',
  'leaveFailed': 'Failed to leave',
  'exportFailed': 'Export failed',
  'cancelFailed': 'Failed to cancel',
  'dndSetFailed': 'Failed to set DND',
  'testFailed': 'Test failed',
  'avatarTooLarge': 'Avatar must be less than 2MB',
}

export const errorJa: TranslationDict = {
  'unauthorized': '認証エラー',
  'requestFailed': 'リクエスト失敗 ({status})',
  'uploadFailed': 'アップロード失敗 ({status})',
  'unknown': '不明なエラー',
  'saveFailed': '保存に失敗しました',
  'createAgentFailed': '作成に失敗しました',
  'startDmFailed': 'DMの開始に失敗しました',
  'operationFailed': '操作に失敗しました',
  'invalidMinutes': '有効な分数を入力してください',
  'dndMaxDuration': 'おやすみモードの最大期間は7日間（10080分）です',
  'leaveFailed': '退出に失敗しました',
  'exportFailed': 'エクスポートに失敗しました',
  'cancelFailed': 'キャンセルに失敗しました',
  'dndSetFailed': '設定に失敗しました',
  'testFailed': 'テストに失敗しました',
  'avatarTooLarge': 'アバターは2MB以下にしてください',
}
