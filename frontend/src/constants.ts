// 用户手册（内部路由，React 页面渲染）
export const MANUAL_URL = '/manual'
// 管理与开发者手册（统一使用 ManualPage 渲染）
export const ADMIN_MANUAL_URL = '/manual/admin'

// ============================================================
// AI/用户在线状态颜色（统一数据源，避免散落硬编码）
// ============================================================

/** 状态指示圆点颜色（小圆点，纯背景色） */
export const STATE_DOT_COLORS: Record<string, string> = {
  active: 'bg-mint-400',
  dnd: 'bg-rose-400',
  inactive: 'bg-border',
}

/** 获取状态指示圆点的 Tailwind 背景色类名 */
export function getStateDotColor(state: string | null | undefined): string {
  if (!state) return STATE_DOT_COLORS.inactive
  return STATE_DOT_COLORS[state] ?? STATE_DOT_COLORS.inactive
}

/** 状态徽章颜色（背景+文字+边框组合，用于卡片/列表标签） */
export const STATE_BADGE_COLORS: Record<string, string> = {
  active: 'bg-mint-400/15 text-mint-400 border-mint-400/30',
  dnd: 'bg-rose-400/15 text-rose-400 border-rose-400/30',
  inactive: 'bg-border text-textSecondary border-border/30',
  blocked: 'bg-accent-400/15 text-accent-400 border-accent-400/30',
}

/** 状态中文标签 */
export const STATE_LABELS: Record<string, string> = {
  active: '在线',
  dnd: '勿扰',
  inactive: '离线',
  blocked: '封禁',
}

/** 状态标签颜色（小标签/药丸样式，用于工具和技能管理面板） */
export const STATE_TAG_COLORS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  dnd: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  inactive: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
  blocked: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
}

// ============================================================
// AI 类型标签（统一数据源，避免 AgentsPage / MePage 重复定义）
// ============================================================

/** AI 类型 → i18n 键名 + Tailwind 颜色 */
export const AI_TYPE_LABEL: Record<string, { key: string; cls: string }> = {
  resonance: { key: 'aiType.resonance', cls: 'bg-violet-500/10 text-violet-400' },
  general: { key: 'aiType.general', cls: 'bg-primary-500/10 text-primary-400' },
  semi_general: { key: 'aiType.semiGeneral', cls: 'bg-amber-500/10 text-amber-400' },
}

// ============================================================
// chat-refresh 事件（ChatView ↔ ChatSidebar 兄弟通信）
// ============================================================

export const CHAT_REFRESH_EVENT = 'chat-refresh'

export type ChatRefreshEventType = 'message_sent' | 'unread_update' | 'dm_notification'

export interface ChatRefreshDetail {
  type: ChatRefreshEventType
  conversation_type?: 'group' | 'dm'
  conversation_id?: number | string
}
