/**
 * 前端页面地址的唯一来源。
 *
 * 同一页面的「后端接口路径」与「前端页面地址」不是一回事：日志接口叫
 * `/admin/conversation-log`（LogBrowser 的 basePath），页面地址却是 `/admin?tab=convlog`。
 * 2026-11-26 的 404 就是把接口路径当页面地址写进 navigate() 造成的。
 * 路由再搬家时只改这里，不去追散落在各处的字符串。
 */

/** 控制台页面（ConsolePage）的地址：页签由 ?tab= 决定 */
export const CONSOLE_PATH = '/admin'

/** 控制台页签地址；params 并入 query（值统一转字符串） */
export function consoleTab(tab: string, params?: Record<string, string | number>): string {
  const query = new URLSearchParams({ tab })
  for (const [key, value] of Object.entries(params ?? {})) query.set(key, String(value))
  return `${CONSOLE_PATH}?${query.toString()}`
}
