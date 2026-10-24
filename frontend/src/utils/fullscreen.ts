/**
 * 全屏（浏览器 Fullscreen API）的唯一封装。
 *
 * 为什么单独一个文件：沉浸页顶栏、设计页预览以后都可能加全屏入口；
 * vendor 判断、fullscreenEnabled、事件订阅各写一遍，早晚只改到一处。
 * 调用方只做两件事：按钮 → toggleFullscreen()；状态 → onFullscreenChange。
 */

/** 当前环境是否允许全屏（嵌入式 iframe 未授权时为 false——此时不要渲染按钮） */
export function fullscreenSupported(): boolean {
  return typeof document !== 'undefined' && document.fullscreenEnabled
}

/** 当前是否处于全屏 */
export function isFullscreen(): boolean {
  return typeof document !== 'undefined' && document.fullscreenElement != null
}

/**
 * 切换全屏。浏览器可能拒绝（没有用户手势、iframe 未授权 fullscreen），
 * 返回是否成功；按钮状态一律以 fullscreenchange 事件为准，不要乐观看返回值。
 */
export async function toggleFullscreen(target: HTMLElement = document.documentElement): Promise<boolean> {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await target.requestFullscreen()
    return true
  } catch {
    return false
  }
}

/** 订阅全屏状态变化，返回取消订阅函数 */
export function onFullscreenChange(handler: (on: boolean) => void): () => void {
  const listener = () => handler(isFullscreen())
  document.addEventListener('fullscreenchange', listener)
  return () => document.removeEventListener('fullscreenchange', listener)
}
