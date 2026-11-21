import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { isAppleTouch } from '../utils/platform'

/**
 * 平台自带的一根滚动指示条（原生滚动条已被 .pb-nav / .chat-scroll 收起，聊天页全端都用它）。
 *
 * 原生滚动条按滚动容器的整条高度绘制，末端会从悬浮底部导航、悬浮输入区旁边穿出去；
 * 而它没法单独缩短到它们的上沿。这里自己画一根 4px 的圆头细条：轨道止于那颗浮层的顶边，
 * 内容仍然从浮层下面穿过（玻璃效果保留），两件事互不干扰。
 *
 * 滚动期间**不触发 React 渲染**：轨道几何（top/height）用 state 存，只有容器尺寸变化才更新；
 * 滑块长短跟着内容走，由内容层 ResizeObserver 直接写 DOM；
 * 滑块位置优先交给 CSS 滚动驱动动画（合成线程按滚动进度算，与内容同帧，主线程不参与），
 * 不支持的浏览器再由滚动回调每帧写 translate。每帧 setState 会让这台机器上的滚动明显发涩。
 *
 * 只负责找当前页面的滚动容器：传了 containerRef 就用它，否则 main 自己滚就是 main，
 * 再否则取第一个挂了 .pb-nav 且确实溢出的容器（会话列表、卡片列表这类页面自己滚的情况）。
 */
// 滚动驱动动画（CSS scroll-driven animations）：滑块位置由合成线程按滚动进度算，
// 内容动一帧、滑块跟一帧，主线程忙也不会滞后。支持情况（2026-11）：
// Chrome/Edge 115+（2023-07）、Safari/iOS 26+（2025-09）、Firefox 159+（2026-10 已发布）。
// 不支持的浏览器回退到 JS 每帧写位移，行为与以前一致。
let timelineSeq = 0
const boundScopes = new Set<string>()
const syncTimelineScope = () => {
  const list = [...boundScopes]
  if (list.length) document.body.style.setProperty('timeline-scope', list.join(', '))
  else document.body.style.removeProperty('timeline-scope')
}
const SUPPORTS_TIMELINE = typeof CSS !== 'undefined' && typeof CSS.supports === 'function'
  && CSS.supports('animation-timeline: scroll()')
  && CSS.supports('scroll-timeline-name: --a')
  && CSS.supports('timeline-scope: --a')

/** 滑块长度与可走距离都由「容器高 / 内容高」算：内容一旦长高（进场时还没加载完、流式输出、
 *  图片撑开）这两样都得重算，否则会留着进场时那根满高的条，滑块也按旧距离走——看着就是"变慢"。 */
const thumbMetrics = (el: HTMLElement, trackHeight: number) => {
  const ratio = Math.min(1, el.clientHeight / Math.max(1, el.scrollHeight))
  const thumbHeight = Math.max(28, trackHeight * ratio)
  return { thumbHeight, travel: Math.max(0, trackHeight - thumbHeight) }
}

interface Track {
  top: number
  height: number
  thumbHeight: number
  /** 可滚动距离（scrollHeight - clientHeight）：滚动回调里只用它，不再读布局 */
  max: number
}

interface Props {
  /** 指定滚动容器；不传就按页面自动找 main / 第一个溢出的 .pb-nav 容器 */
  containerRef?: React.RefObject<HTMLElement | null>
  /** 桌面上也画。聊天页全端都用这一根：原生滚动条已被 .chat-scroll 收起，
   *  桌面上本来就没有别的指示器，不该只有手机看得到 */
  always?: boolean
}

/** iOS / iPadOS 用系统指示器：它的 overlay 条藏不掉，自绘只会变成第二条 */
const SYSTEM_INDICATOR_ONLY = isAppleTouch()

export default function MobileScrollbar({ containerRef, always = false }: Props) {
  const [track, setTrack] = useState<Track | null>(null)
  const [timelineName] = useState(() => `--scrollbar-${++timelineSeq}`)
  const targetRef = useRef<HTMLElement | null>(null)
  const geoRef = useRef<Track | null>(null)
  const trackElRef = useRef<HTMLDivElement | null>(null)
  const travelElRef = useRef<HTMLDivElement | null>(null)
  const thumbElRef = useRef<HTMLDivElement | null>(null)

  /** 只改 transform：滚动时每帧走这里，不进 React */
  const paint = useCallback(() => {
    const el = targetRef.current
    const geo = geoRef.current
    // 位移写在"能走的那一段"（travel）上，不是滑块自己：动画的 100% 得是整段可走距离
    const travel = travelElRef.current
    if (!el || !geo || !travel) return
    const progress = Math.min(1, Math.max(0, el.scrollTop / Math.max(1, geo.max)))
    const offset = progress * Math.max(0, geo.height - geo.thumbHeight)
    // 有滚动时间轴时写 translate：动画（若已生效）会盖住它，所以这一份只是"不生效时"的兜底，
    // 两者写的是同一个属性，不会叠成两倍位移
    if (SUPPORTS_TIMELINE) travel.style.translate = `0 ${offset}px`
    else travel.style.transform = `translateY(${offset}px)`
  }, [])

  /** 内容长高了：滑块长度、可走距离直接写 DOM。流式输出时每帧都可能变，不能走 React 渲染 */
  const syncThumb = useCallback(() => {
    const geo = geoRef.current
    const travel = travelElRef.current
    const thumb = thumbElRef.current
    if (!geo || !travel || !thumb) return
    thumb.style.height = `${geo.thumbHeight}px`
    // 动画关键帧是 0 → 100%，百分比按这一层自己的高度算；改高度就等于改可走距离，
    // 关键帧本身不用碰——关键帧里引用实时变量的话，每次内容变化都要重解析一遍
    travel.style.height = `${Math.max(0, geo.height - geo.thumbHeight)}px`
  }, [])

  useEffect(() => {
    if (SYSTEM_INDICATOR_ONLY) return
    let target: HTMLElement | null = null
    let observer: ResizeObserver | null = null
    let idleTimer: number | undefined

    const pick = (): HTMLElement | null => {
      if (containerRef?.current) return containerRef.current
      const main = document.querySelector('main')
      if (!main) return null
      // 只接管「已经关掉原生滚动条」的容器（.pb-nav / .chat-scroll 是项目里唯一的两个标记）：
      // 原生条还在的容器再画一根自绘的，就会并排出现两条——iPad 检测到指针时系统条常驻，
      // 最显眼；iPhone 只在滚动瞬间闪现，所以一直没被发现。
      if (main.classList.contains('pb-nav') && main.scrollHeight > main.clientHeight + 4) return main
      const inner = [...main.querySelectorAll<HTMLElement>('.pb-nav')]
        .find((el) => el.scrollHeight > el.clientHeight + 4)
      return inner ?? null
    }

    // 上一步量出来的几何：没变就不 setTrack（滚动时不必重渲染）
    let last: Track | null = null

    // 内容变高（流式输出、图片撑开、代码块高亮）时更新「可滚动距离、滑块长短、能走的距离」，
    // 但不动轨道几何（上下沿由容器尺寸决定）、也不 setState：流式每帧都涨，重渲染会把滚动拖卡。
    // 这里也不靠"流式输出时记得通知一下"——那种通知会漏（图片加载、表情包渲染、折叠展开都会涨高），
    // 而 ResizeObserver 观察的是内容本身：回调发生在布局之后，读 scrollHeight 不必再排一次版。
    let contentObserver: ResizeObserver | null = null
    let observed: Element[] = []

    // 内容高度变了 → 「可滚动距离 + 滑块长短 + 它能走的距离」一起重算。
    // 只更新可滚动距离是不够的：进场/切会话时内容还没加载完，量出来是一根满高的长条，
    // 之后它就不再变——用户看到的就是"长条一直不恢复"。
    const recalc = () => {
      const geo = geoRef.current
      const t = targetRef.current
      if (!geo || !t) return
      geo.max = Math.max(0, t.scrollHeight - t.clientHeight)
      geo.thumbHeight = thumbMetrics(t, geo.height).thumbHeight
      syncThumb()
      paint()
    }

    const syncContent = (el: HTMLElement) => {
      const next = Array.from(el.children)
      if (next.length === observed.length && next.every((n, i) => n === observed[i])) return
      contentObserver?.disconnect()
      contentObserver = new ResizeObserver(() => {
        const geo = geoRef.current
        const t = targetRef.current
        if (!geo || !t) return
        if (Math.abs(Math.max(0, t.scrollHeight - t.clientHeight) - geo.max) < 0.5) return
        recalc()
      })
      for (const child of next) contentObserver.observe(child)
      observed = next
    }

    // 结构变了（切会话、虚拟列表增删、消息列表整块重挂载）→ 重建内容观察者。
    // 被换掉的节点不会再报尺寸，旧观察者等于失效：滑块会停在切走前那根长条上，
    // 而且再怎么加载也不恢复——这是"切个聊天滑块就变很长"的直接原因。
    let structure: MutationObserver | null = null
    let structureFrame = 0
    const watchStructure = (el: HTMLElement) => {
      structure?.disconnect()
      structure = new MutationObserver(() => {
        if (structureFrame) return
        structureFrame = requestAnimationFrame(() => {
          structureFrame = 0
          const t = targetRef.current
          if (!t) return
          syncContent(t)
          // 结构换了就可能换了"该画的容器"：切页签后内容变短，容器不再溢出时要收掉滑块，
          // 否则它会停在上一页那根进度上（就是"切页后进度串了"）
          measure()
        })
      })
      structure.observe(el, { childList: true })
    }

    const measure = () => {
      // 指定了容器就不看底部导航，轨道底边改由容器声明的 data-composer-inset 决定
      const nav = containerRef?.current ? null : document.querySelector('nav[class*="fixed left-3"]')
      const next = pick()
      if (next !== target) {
        observer?.disconnect()
        contentObserver?.disconnect()
        contentObserver = null
        observed = []
        structure?.disconnect()
        structure = null
        if (structureFrame) { cancelAnimationFrame(structureFrame); structureFrame = 0 }
        detachTimeline(target)
        detachScroll()
        target = next
        targetRef.current = next
        if (target) {
          observer = new ResizeObserver(schedule)
          observer.observe(target)
          if (SUPPORTS_TIMELINE) {
            // 命名时间轴挂在滚动容器上。滑块是 fixed 的独立元素、不是它的后代，
            // 所以还要把时间轴提升到 body 作用域（timeline-scope），否则引用不到
            target.style.setProperty('scroll-timeline-name', timelineName)
            target.style.setProperty('scroll-timeline-axis', 'block')
            boundScopes.add(timelineName)
            syncTimelineScope()
          }
          // passive：滚动监听里不阻止默认行为，交给合成线程处理，别拖住滚动
          target.addEventListener('scroll', onScroll, { passive: true })
          watchStructure(target)
        }
      }
      if (!target || (!nav && !containerRef?.current)) {
        geoRef.current = null
        if (last) { last = null; setTrack(null) }
        return
      }
      const r = target.getBoundingClientRect()
      // 顶部有"条"时轨道要从它的下沿开始，否则会画到头部上面去。两种摆法都要认：
      // ① 页面头（.top-bar）是滚动容器的前一个兄弟（PageShell 就是这种）
      // ② 聊天头部浮在面板上、根本不在滚动容器里（data-float="top"）：沿祖先往上找
      // 只在 measure 里量一次 rect，滚动回调里不碰布局
      let topBar: HTMLElement | null = null
      const prev = target.previousElementSibling as HTMLElement | null
      if (prev && prev.matches('.top-bar')) topBar = prev
      if (!topBar) {
        let host: HTMLElement | null = target.parentElement
        while (host && host !== document.body) {
          const found = host.querySelector<HTMLElement>('[data-float="top"]')
          if (found) { topBar = found; break }
          host = host.parentElement
        }
      }
      const topEdge = Math.max(r.top, topBar ? topBar.getBoundingClientRect().bottom : r.top)
      const top = topEdge + 3
      // 读 data-* 而不是 getComputedStyle：后者每次都强制一次样式重算，滚动时读它会把滚动拖卡
      const inset = containerRef?.current ? Number(target.dataset.composerInset || 0) || 0 : 0
      const bottom = (nav ? Math.min(r.bottom, nav.getBoundingClientRect().top) : r.bottom - inset) - 3
      const height = bottom - top
      const value: Track = {
        top, height,
        thumbHeight: thumbMetrics(target, height).thumbHeight,
        // 可滚动距离在这里量一次：滚动回调每帧都要用它，读 scrollHeight/clientHeight
        // 会强制一次布局，正是手机上滚动发涩的那部分开销
        max: Math.max(0, target.scrollHeight - target.clientHeight),
      }
      geoRef.current = value
      syncContent(target)
      syncThumb()
      paint()
      if (last && Math.abs(last.top - value.top) < 0.5 && Math.abs(last.height - value.height) < 0.5
          && Math.abs(last.thumbHeight - value.thumbHeight) < 0.5) return
      last = value
      setTrack(value)
    }

    // 一帧最多量一次：滚动事件的频率远高于刷新率，逐事件读 getBoundingClientRect 会反复强制布局。
    // 「量几何」与「画滑块」各占一个 rAF：共用一个 id 时，滚动事件会把还没执行的 measure 取消掉，
    // 容器刚变尺寸的那一拍就永远量不到
    let measureFrame = 0
    const schedule = () => {
      if (measureFrame) return
      measureFrame = requestAnimationFrame(() => { measureFrame = 0; measure() })
    }

    let paintFrame = 0
    const onScroll = () => {
      // 只画滑块 + 提透明度（都走 style，不 setState）
      if (!paintFrame) paintFrame = requestAnimationFrame(() => { paintFrame = 0; paint() })
      const el = trackElRef.current
      if (el && el.style.opacity !== '1') el.style.opacity = '1'
      window.clearTimeout(idleTimer)
      idleTimer = window.setTimeout(() => {
        if (trackElRef.current) trackElRef.current.style.opacity = '0.55'
      }, 700)
    }

    const detachScroll = () => {
      target?.removeEventListener('scroll', onScroll)
    }

    const detachTimeline = (el: HTMLElement | null) => {
      if (!el || !SUPPORTS_TIMELINE) return
      el.style.removeProperty('scroll-timeline-name')
      el.style.removeProperty('scroll-timeline-axis')
      boundScopes.delete(timelineName)
      syncTimelineScope()
    }

    // 这里只盯一件事：页面换了（滚动容器跟着换）。所以只观察 main 的**直接子节点**，
    // 不递归看子树——流式输出每帧都在改深层 DOM，那是上面 contentObserver 的活儿。
    // 以前这里监的是整棵 main，流式时每帧都变，只能靠 250ms 节流兜住，那一下是强制布局。
    const mutation = new MutationObserver(() => { measure() })
    const root = document.querySelector('main')
    if (root) mutation.observe(root, { childList: true })

    measure()
    window.addEventListener('resize', schedule)
    return () => {
      observer?.disconnect()
      contentObserver?.disconnect()
      structure?.disconnect()
      detachTimeline(target)
      detachScroll()
      mutation.disconnect()
      window.removeEventListener('resize', schedule)
      window.clearTimeout(idleTimer)
      if (measureFrame) cancelAnimationFrame(measureFrame)
      if (paintFrame) cancelAnimationFrame(paintFrame)
      if (structureFrame) cancelAnimationFrame(structureFrame)
    }
  }, [containerRef, paint, timelineName])

  /** 按下轨道即跳转 + 拖动：原生滚动条在手机断点内被收起，这根得能当滚动条用 */
  const onPointerDown = (e: React.PointerEvent) => {
    const el = targetRef.current
    const geo = geoRef.current
    if (!el || !geo) return
    e.preventDefault()
    const trackLen = geo.height - geo.thumbHeight
    const scrollLen = el.scrollHeight - el.clientHeight
    if (trackLen <= 0 || scrollLen <= 0) return
    const startY = e.clientY
    // 点在滑块外：先把滑块中心挪到指针位置，再接着拖
    const thumbOffset = (el.scrollTop / scrollLen) * trackLen
    const topInTrack = geo.top + thumbOffset
    if (e.clientY < topInTrack || e.clientY > topInTrack + geo.thumbHeight) {
      el.scrollTop = ((e.clientY - geo.top - geo.thumbHeight / 2) / trackLen) * scrollLen
    }
    const startTop = el.scrollTop
    const ratio = scrollLen / trackLen
    const move = (ev: PointerEvent) => {
      el.scrollTop = startTop + (ev.clientY - startY) * ratio
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }

  if (SYSTEM_INDICATOR_ONLY || !track) return null
  return (
    <div
      // 不挂 aria-hidden：它现在能点能拖，藏起来等于骗读屏。也不挂 role="scrollbar"——
      // 真正的滚动语义在滚动容器自己身上（键盘、读屏都走原生那条路），这里只是一根视觉代理，
      // 再声明一遍只会在两者之间制造"谁是真相"的问题
      // 外壳 16px 宽只为了好按（轨道看着仍是 4px）；touch-none 免得拖动时页面跟着滚
      className={`${always ? '' : 'md:hidden '}fixed right-0 z-overlay w-4 touch-none cursor-pointer`}
      style={{ top: track.top, height: track.height }}
      onPointerDown={onPointerDown}
    >
      <div
        ref={trackElRef}
        className="absolute right-[2px] w-[4px] rounded-full bg-primary-500/15 transition-opacity duration-300"
        style={{ top: 0, height: '100%', opacity: 0.55 }}
      >
        {/* 两层：外层高度 = 可走距离（动画 0 → 100% 按它换算成像素），内层才是那 4px 滑块。
            这样关键帧是纯静态百分比，不引用任何实时变量——变量一变就要重解析关键帧，
            滚动动画就容易被挪出合成线程（放大界面/内容加载都会改它）。 */}
        <div
          ref={travelElRef}
          className="absolute left-0 top-0 w-full"
          style={{
            height: Math.max(0, track.height - track.thumbHeight),
            willChange: SUPPORTS_TIMELINE ? 'translate' : 'transform',
            // 位置交给滚动时间轴：内容动一帧、滑块跟一帧（合成线程算，主线程不参与）。
            // 不支持时上面的 paint() 每帧写位移，两套写法不叠加：都写 translate。
            ...(SUPPORTS_TIMELINE ? {
              animationName: 'x-scrollbar-thumb',
              animationDuration: '1s',
              animationTimingFunction: 'linear',
              animationTimeline: timelineName,
              // 终点放到 100.1%：让滚动到底时动画仍是 running。进度一旦到 100%，动画就被判 finished，
              // 合成器不再驱动它，再往回滚虽然 playState 回到 running，帧率却掉回主线程那条路
              // （headless 实测：无这段 range 时滚到 max 得到 finished，有则全程 running）。
              animationRange: '0% 100.1%',
              // 必须 fill：滚动到底时没有 fill 会把位移整个撤掉，滑块在最后那一格弹回顶部
              // （headless 实测：滚到 max 时 computed translate 变回 none）
              animationFillMode: 'both',
            } as CSSProperties : null),
          }}
        >
          <div
            ref={thumbElRef}
            className="w-full rounded-full bg-textMuted/70"
            style={{ height: track.thumbHeight }}
          />
        </div>
      </div>
    </div>
  )
}
