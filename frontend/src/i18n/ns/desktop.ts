/**
 * desktop 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('desktop:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const desktopZh: TranslationDict = {

  // ======================== 桌面端 / Desktop ========================
  'instanceSetupTitle': '连接实例',
  'instanceSetupDesc': '请输入 Copree 实例地址以连接到服务器。',
  'instanceUrlLabel': '实例地址',
  'instanceUrlPlaceholder': '例: https://example.com',
  'testConnection': '测试连接',
  'testing': '测试连接中...',
  'testSuccess': '连接成功',
  'testFailed': '连接失败，请检查地址是否正确',
  'saveAndContinue': '保存并继续',
  'skip': '跳过，稍后设置',
  'localModelTitle': '本地模型',
  'localModelDesc': '管理本地运行的 AI 模型服务（Ollama）。',
  'connected': '已连接',
  'disconnected': '未连接',
  'installedModels': '已安装模型',
  'noModels': '未检测到已安装的模型',
  'startService': '启动服务',
  'stopService': '停止服务',
  'defaultModel': '设为默认',
  'isDefault': '当前默认',
  'refresh': '刷新',
  'ollamaNotRunning': '未检测到 Ollama 服务，请先启动',
  'detecting': '检测中...',
}

export const desktopEn: TranslationDict = {
  'instanceSetupTitle': 'Connect Instance',
  'instanceSetupDesc': 'Enter the Copree instance address to connect to the server.',
  'instanceUrlLabel': 'Instance URL',
  'instanceUrlPlaceholder': 'e.g. https://example.com',
  'testConnection': 'Test Connection',
  'testing': 'Testing...',
  'testSuccess': 'Connection successful',
  'testFailed': 'Connection failed, please check the URL',
  'saveAndContinue': 'Save & Continue',
  'skip': 'Skip for now',
  'localModelTitle': 'Local Models',
  'localModelDesc': 'Manage locally running AI model services (Ollama).',
  'connected': 'Connected',
  'disconnected': 'Disconnected',
  'installedModels': 'Installed Models',
  'noModels': 'No models detected',
  'startService': 'Start Service',
  'stopService': 'Stop Service',
  'defaultModel': 'Set as Default',
  'isDefault': 'Current Default',
  'refresh': 'Refresh',
  'ollamaNotRunning': 'Ollama is not running. Please start it first.',
  'detecting': 'Detecting...',
}

export const desktopJa: TranslationDict = {
  'instanceSetupTitle': 'インスタンス接続',
  'instanceSetupDesc': 'Copree インスタンスのアドレスを入力してサーバーに接続します。',
  'instanceUrlLabel': 'インスタンスURL',
  'instanceUrlPlaceholder': '例: https://example.com',
  'testConnection': '接続テスト',
  'testing': 'テスト中...',
  'testSuccess': '接続成功',
  'testFailed': '接続失敗。URLを確認してください',
  'saveAndContinue': '保存して続行',
  'skip': '後で設定',
  'localModelTitle': 'ローカルモデル',
  'localModelDesc': 'ローカルで実行中のAIモデルサービス（Ollama）を管理します。',
  'connected': '接続済み',
  'disconnected': '未接続',
  'installedModels': 'インストール済みモデル',
  'noModels': 'インストール済みモデルが見つかりません',
  'startService': 'サービス起動',
  'stopService': 'サービス停止',
  'defaultModel': 'デフォルトに設定',
  'isDefault': '現在のデフォルト',
  'refresh': '更新',
  'ollamaNotRunning': 'Ollamaが実行されていません。先に起動してください。',
  'detecting': '検出中...',
}
