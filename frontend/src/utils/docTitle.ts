/**
 * 标签页标题的唯一出口。
 *
 * 未读计数（useDesktopNotification）与新消息闪烁（useWebSocket）本来各写各的 document.title：
 * 闪烁的定时器把计数标题顶掉，聚焦时又把闪烁中的标题当成"原标题"存下来，回来后留下的是
 * 残留的计数或半截闪烁文案，两个 interval 还会以 800ms/1000ms 互相覆盖。
 * 这里把两者收成一份状态——一个未读数 + 一个闪烁定时器，标题只在 apply() 里算出来。
 */

export const BASE_TITLE = 'Copree'
const FLASH_INTERVAL_MS = 800

let unread = 0
let flashLabel = ''
let flashTimer: ReturnType<typeof setInterval> | null = null
let flashSide = false        // 闪烁当前停在哪一面
let countVisible = false     // 窗口失焦：标签页还看得见也要显示计数（原实现在 blur 时立刻显示）

function idleTitle(): string {
  // 计数只在"人不在看"时露出来；人在页面上还挂着 (3) 只是干扰
  return (document.hidden || countVisible) && unread > 0 ? `(${unread}) ${BASE_TITLE}` : BASE_TITLE
}

function apply(): void {
  document.title = flashTimer && flashSide ? `${flashLabel} · ${BASE_TITLE}` : idleTitle()
}

/** 未读数变化：闪烁中只更新"另一面"要显示的计数，不打断闪烁 */
export function setUnreadCount(count: number): void {
  unread = Math.max(0, count)
  if (!flashTimer) apply()
}

/** 窗口聚焦/失焦：失焦时计数要显示出来，聚焦时收回 */
export function setCountVisible(visible: boolean): void {
  countVisible = visible
  if (!flashTimer) apply()
}

/** 有新消息：交替「闪烁文案 ↔ 计数标题」；已在闪则只换文案 */
export function startTitleFlash(label: string): void {
  flashLabel = label
  if (flashTimer) { apply(); return }
  flashSide = true
  apply()
  flashTimer = setInterval(() => {
    flashSide = !flashSide
    apply()
  }, FLASH_INTERVAL_MS)
}

/** 停下闪烁，回到按未读算的标题 */
export function stopTitleFlash(): void {
  if (flashTimer) {
    clearInterval(flashTimer)
    flashTimer = null
  }
  flashSide = false
  apply()
}
