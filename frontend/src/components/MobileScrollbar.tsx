import { useEffect, useState } from 'react'

/**
 * 手机端的滚动指示条（原生滚动条已被 .pb-nav 收起）。
 *
 * 原生滚动条按滚动容器的整条高度绘制，末端会从悬浮底部导航旁边穿出去；
 * 而它没法单独缩短到导航上沿。这里自己画一根 3px 的圆头细条：
 * 轨道止于导航顶边，内容仍然从导航下面穿过（玻璃效果保留），
 * 两件事互不干扰，也不用再考虑「滚动条被导航压住」。
 *
 * 只负责找当前页面的滚动容器：main 自己滚就是 main，否则取第一个挂了
 * .pb-nav 且确实溢出的容器（会话列表、卡片列表这类页面自己滚的情况）。
 */
interface Track {
  top: number
  height: number
  thumbTop: number
  thumbHeight: number
}

export default function MobileScrollbar() {
  const [track, setTrack] = useState<Track | null>(null)
  const [scrolling, setScrolling] = useState(false)

  useEffect(() => {
    let target: HTMLElement | null = null
    let observer: ResizeObserver | null = null
    let idleTimer: number | undefined

    const pick = (): HTMLElement | null => {
      const main = document.querySelector('main')
      if (!main) return null
      if (main.scrollHeight > main.clientHeight + 4) return main
      const inner = [...main.querySelectorAll<HTMLElement>('.pb-nav')]
        .find((el) => el.scrollHeight > el.clientHeight + 4)
      return inner ?? null
    }

    // 上一步量出来的值：跟新值相等就不 setTrack，避免每秒一次无意义的重渲染
    let last: Track | null = null

    const measure = () => {
      const nav = document.querySelector('nav[class*="fixed left-3"]')
      const next = pick()
      if (next !== target) {
        observer?.disconnect()
        detachScroll()
        target = next
        if (target) {
          observer = new ResizeObserver(schedule)
          observer.observe(target)
          // passive：滚动监听里不阻止默认行为，交给合成线程处理，别拖住滚动
          target.addEventListener('scroll', onScroll, { passive: true })
        }
      }
      if (!target || !nav) {
        if (last) { last = null; setTrack(null) }
        return
      }
      const r = target.getBoundingClientRect()
      const navTop = nav.getBoundingClientRect().top
      const top = Math.max(r.top, 0) + 3
      const bottom = Math.min(r.bottom, navTop) - 3
      const height = bottom - top
      const ratio = target.clientHeight / Math.max(1, target.scrollHeight)
      const thumbHeight = Math.max(28, height * ratio)
      const progress = target.scrollTop / Math.max(1, target.scrollHeight - target.clientHeight)
      const value: Track = { top, height, thumbTop: top + progress * Math.max(0, height - thumbHeight), thumbHeight }
      if (last && Math.abs(last.top - value.top) < 0.5 && Math.abs(last.height - value.height) < 0.5
          && Math.abs(last.thumbTop - value.thumbTop) < 0.5 && Math.abs(last.thumbHeight - value.thumbHeight) < 0.5) return
      last = value
      setTrack(value)
    }

    // 一帧最多量一次：滚动事件的频率远高于刷新率，逐事件读 getBoundingClientRect 会反复强制布局
    let frame = 0
    const schedule = () => {
      if (frame) return
      frame = requestAnimationFrame(() => { frame = 0; measure() })
    }

    const onScroll = () => {
      schedule()
      setScrolling(true)
      window.clearTimeout(idleTimer)
      idleTimer = window.setTimeout(() => setScrolling(false), 700)
    }

    const detachScroll = () => {
      target?.removeEventListener('scroll', onScroll)
    }

    // 页面切换、数据加载完、图片撑高，滚动容器都会变；只在 DOM 变化后重量一次。
    // 合并 250ms 内的连续变更：聊天流式输出时 DOM 每帧都在变，逐次量会不停触发强制布局
    let pending = 0
    const remeasureAfterChange = () => {
      if (pending) return
      pending = window.setTimeout(() => { pending = 0; measure() }, 250)
    }
    const mutation = new MutationObserver(remeasureAfterChange)
    const root = document.querySelector('main')
    if (root) mutation.observe(root, { childList: true, subtree: true })

    measure()
    window.addEventListener('resize', schedule)
    return () => {
      observer?.disconnect()
      detachScroll()
      mutation.disconnect()
      window.removeEventListener('resize', schedule)
      window.clearTimeout(idleTimer)
      window.clearTimeout(pending)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  if (!track) return null
  return (
    <div
      aria-hidden="true"
      className="md:hidden pointer-events-none fixed right-[3px] z-overlay w-[3px] rounded-full bg-primary-500/20 transition-opacity duration-300"
      style={{ top: track.top, height: track.height, opacity: scrolling ? 1 : 0.55 }}
    >
      <div
        className="absolute left-0 w-full rounded-full bg-primary-500/80"
        style={{ top: track.thumbTop - track.top, height: track.thumbHeight }}
      />
    </div>
  )
}
