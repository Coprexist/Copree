/**
 * 「滚到某一段」的唯一实现：先滚过去，滚停之后再校准一次落点。
 *
 * 不能一次算准绝对位置：内容会迟到的重排（分片补渲染、字体、图片、折叠动画），点击那一刻量到的
 * 位置，到真正滚过去时可能已经差出两三百像素（对话日志里实测差 237px，再点一次才准）。
 * 所以先按「当前偏差」相对滚，等滚动真的停下来再看落点，还歪就再校一次。
 */

/** 目标贴在滚动容器顶部往下这么多（标题不贴边） */
const DEFAULT_GAP = 16
/** 落点容差：差这么多像素以内就算对上了 */
const TOLERANCE = 4

interface Target {
  scroller: HTMLElement | null
  target: HTMLElement | null
}

/** 让 target 对齐到 scroller 顶部往下的 gap 处；要动就动，一次算完返回是否找到了元素 */
function align(resolve: () => Target, gap: number, behavior: ScrollBehavior): boolean {
  const { scroller, target } = resolve()
  if (!scroller || !target) return false
  const offset = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top
  if (Math.abs(offset - gap) > TOLERANCE) {
    scroller.scrollTo({ top: Math.max(0, scroller.scrollTop + offset - gap), behavior })
  }
  return true
}

export function scrollToAnchor(
  resolve: () => Target,
  { gap = DEFAULT_GAP, frames = 8, settleMs = 3000 }: { gap?: number; frames?: number; settleMs?: number } = {},
): void {
  requestAnimationFrame(function start() {
    if (align(resolve, gap, 'smooth')) return settle()
    if (--frames > 0) requestAnimationFrame(start)
  })

  /** 等滚动停下来：还在动就继续等，停住了还歪（迟到的重排）就直接校准 */
  function settle() {
    const deadline = performance.now() + settleMs
    let lastTop = -1
    let still = 0
    let timer = 0
    const step = () => {
      if (performance.now() > deadline) return
      const { scroller, target } = resolve()
      if (!scroller || !target) return
      const offset = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top
      if (Math.abs(offset - gap) <= TOLERANCE) return
      const top = Math.round(scroller.scrollTop)
      if (top === lastTop && ++still >= 2) {
        align(resolve, gap, 'auto')
        still = 0
        lastTop = -1
      } else if (top !== lastTop) {
        lastTop = top
        still = 0
      }
      timer = window.setTimeout(step, 100)
    }
    timer = window.setTimeout(step, 120)
    // 页面关掉/切走时别再跑
    window.addEventListener('pagehide', () => window.clearTimeout(timer), { once: true })
  }
}
