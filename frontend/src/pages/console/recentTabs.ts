/**
 * 管理台「最近访问」：只存页签 key，不存内容。
 *
 * 首页要能"一眼看到刚才在弄什么"（大厂控制台首页都有这一栏）。
 * localStorage 读写一律兜住异常：存不下最多少一栏，不能让首页打不开。
 */
const KEY = 'console_recent_tabs'

/** 最多记几个：够点回去就行，多了首页那栏会变成第二份导航 */
const MAX = 5

export function rememberTab(key: string): void {
  try {
    const kept = recentTabs().filter(k => k !== key)
    localStorage.setItem(KEY, JSON.stringify([key, ...kept].slice(0, MAX)))
  } catch {
    /* 隐私模式/配额满：忽略 */
  }
}

export function recentTabs(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter(k => typeof k === 'string') : []
  } catch {
    return []
  }
}
