import { RefObject, useLayoutEffect, useRef } from 'react'

/**
 * FLIP —— 内容增删后，让「位置变了但还在」的元素平滑滑到新位置。
 *
 * 为什么需要它：容器高度可以靠 transition 过渡（见 ui/AutoHeight），但元素在父级布局里的
 * top 是随 DOM 增删**瞬间**算出来的，高度过渡不会带着它们走。这里量一次前后位置，
 * 反向 translate 回去再过渡到 0，看起来就是「被推着滑过去」。
 *
 * 坐标一律用**相对容器**的 top（不是相对视口）：容器自身在页面里移动（整页居中时，
 * 卡片被撑高会上移）不该算进位移，否则会和容器动画叠成两段、看着发飘。
 *
 * 用法：元素带 `data-flip="唯一名"`，容器 ref 传进来，deps 填「会引起增删的状态」。
 * 新出现的元素没有旧位置 → 自动跳过（入场动画由调用方自己挂）。
 */
export function useLayoutShift(scopeRef: RefObject<HTMLElement | null>, deps: unknown[]) {
  const prev = useRef<Map<string, number> | null>(null)

  useLayoutEffect(() => {
    const scope = scopeRef.current
    if (!scope) return
    const scopeTop = scope.getBoundingClientRect().top
    const items = Array.from(scope.querySelectorAll<HTMLElement>('[data-flip]'))

    const next = new Map<string, number>()
    for (const el of items) {
      const key = el.dataset.flip
      if (!key) continue
      next.set(key, el.getBoundingClientRect().top - scopeTop)
    }

    // 系统开了「减少动态效果」：位置照记（下次还得比），但一步都不动。
    // 低端机 / 省电模式也常落在这里，等于给这类设备一个近似"关掉"的开关。
    const reduceMotion = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches

    if (prev.current && !reduceMotion) {
      // 先把"旧位置"全部写成 transform（纯写，不读布局），再一次性落地。
      // 反过来的写法（写一个读一个 offsetHeight）每个元素都会触发一次强制同步布局，
      // 8 个元素就是 8 次；这里压成 1 次。
      const moved: Array<{ el: HTMLElement; dy: number }> = []
      for (const el of items) {
        const key = el.dataset.flip
        const before = key ? prev.current.get(key) : undefined
        const after = key ? next.get(key) : undefined
        if (before === undefined || after === undefined) continue
        const dy = before - after
        if (Math.abs(dy) < 0.5) continue
        moved.push({ el, dy })
      }
      for (const { el, dy } of moved) {
        el.style.transition = 'none'
        el.style.transform = `translateY(${dy}px)`
      }
      if (moved.length) {
        void scope.offsetHeight // 唯一一次强制布局：把"旧位置"落成一帧，否则两帧会合并
        for (const { el } of moved) {
          el.style.transition = 'transform 220ms cubic-bezier(.4, 0, .2, 1)'
          el.style.transform = ''
        }
      }
    }

    prev.current = next
    // deps 由调用方给（就是那几个会引起增删的状态），这里不展开
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
