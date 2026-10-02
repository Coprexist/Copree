/**
 * 对话日志分区（i18n 命名空间思路）
 *
 * 独立文件维护：请求体分块（系统提示 / 状态栈 / 插入的消息 / 工具调用 / 工具返回 / 思考 / 收尾报错…）、
 * 一轮对话的结局标签（正常 / 无输出 / 被收尾 / 报错）与状态帧身份标签。
 * 与 tool.ts、admin_config.ts 同模式：translations.ts 运行时合并，前端用 t('logs:xxx') 取，
 * key 在分区内不重复写前缀。
 */

import type { TranslationDict } from './translations'

export const logsZh: TranslationDict = {
  latestRequest: '最新一次请求体',
  empty: '这条日志没有正文。',
  viewRaw: '原始 JSON',
  viewSegments: '分段视图',
  kindSystem: '系统提示',
  kindState: '状态栈',
  kindInjected: '插入的消息',
  kindUser: '收到的话',
  kindAssistant: 'AI 说的话',
  kindRoundTools: '本轮工具',
  kindToolCall: '工具调用',
  kindToolResult: '工具返回',
  kindReasoning: '思考',
  kindError: '收尾/报错',
  statusOk: '正常',
  statusNoOutput: '无输出',
  statusWrapup: '被收尾',
  statusError: '报错',
  stateNone: '无状态',
}

export const logsEn: TranslationDict = {
  latestRequest: 'Latest request body',
  empty: 'This log has no content.',
  viewRaw: 'Raw JSON',
  viewSegments: 'Segmented view',
  kindSystem: 'System prompt',
  kindState: 'State stack',
  kindInjected: 'Injected message',
  kindUser: 'Incoming message',
  kindAssistant: 'AI reply',
  kindRoundTools: 'This round tools',
  kindToolCall: 'Tool call',
  kindToolResult: 'Tool result',
  kindReasoning: 'Reasoning',
  kindError: 'Wrap-up / error',
  statusOk: 'OK',
  statusNoOutput: 'No output',
  statusWrapup: 'Wrapped up',
  statusError: 'Error',
  stateNone: 'No state',
}

export const logsJa: TranslationDict = {
  latestRequest: '最新のリクエストボディ',
  empty: 'このログには本文がありません。',
  viewRaw: '生の JSON',
  viewSegments: 'セグメント表示',
  kindSystem: 'システムプロンプト',
  kindState: '状態スタック',
  kindInjected: '挿入されたメッセージ',
  kindUser: '受信した発言',
  kindAssistant: 'AI の発言',
  kindRoundTools: '今回のツール',
  kindToolCall: 'ツール呼び出し',
  kindToolResult: 'ツール応答',
  kindReasoning: '思考',
  kindError: '終了・エラー',
  statusOk: '正常',
  statusNoOutput: '出力なし',
  statusWrapup: '打ち切り',
  statusError: 'エラー',
  stateNone: '状態なし',
}
