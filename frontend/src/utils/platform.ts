/**
 * 平台检测工具
 * 用于区分 Web 端和桌面端（Tauri）环境
 */

/** 是否运行在 Tauri 桌面端环境中 */
export function isDesktop(): boolean {
  return '__TAURI_INTERNALS__' in window
}

/** 获取实例地址：桌面端从 localStorage 读取，Web 端用当前 origin */
export function getInstanceUrl(): string {
  if (isDesktop()) {
    const stored = localStorage.getItem('instance_url')
    if (stored) return stored.replace(/\/+$/, '') // 去掉末尾斜杠
  }
  // iOS Safari 个别场景（外部链接/PWA 进入）origin 可能首帧未就绪 → 回退 location.href 的 origin
  const origin = window.location.origin || `${window.location.protocol}//${window.location.host}`
  return origin
}

/** 获取 API 基础路径 */
export function getApiBase(): string {
  return `${getInstanceUrl()}/api`
}

/** 获取 WebSocket URL：token 必须 URL 编码（JWT 可能含 +/= 特殊字符，Safari 严格解析会建连失败） */
export function getWsUrl(token?: string): string {
  const instanceUrl = getInstanceUrl()
  const protocol = instanceUrl.startsWith('https') ? 'wss' : 'ws'
  const host = instanceUrl.replace(/^https?:\/\//, '')
  const tokenPart = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${protocol}://${host}/ws${tokenPart}`
}

/** 解析 WebSocket URL 为协议和主机部分（纯函数） */
export function parseWsUrl(url: string): { protocol: 'wss' | 'ws'; host: string } | null {
  const m = url.match(/^(wss?):\/\/([^/]+)/)
  if (!m) return null
  return { protocol: m[1] as 'wss' | 'ws', host: m[2] }
}

/** 是否 iOS / iPadOS：滚动指示器是系统 overlay，CSS 藏不掉，再自绘就是滚动时并排两条 */
export function isAppleTouch(): boolean {
  if (typeof navigator === 'undefined') return false
  const ua = navigator.userAgent || ''
  if (/iPad|iPhone|iPod/.test(ua)) return true
  // iPadOS 13 起桌面模式会伪装成 Macintosh，只能靠触摸点数认
  return navigator.platform === 'MacIntel' && (navigator.maxTouchPoints || 0) > 1
}
