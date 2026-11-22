import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import LoginBrand from './LoginBrand'

/**
 * 进入平台的过场：从提交按钮处拉出一枚同色的紫圆，铺满屏幕，文字全程锚在原位
 * 不移动，依次念出「验证中 → 验证成功！正在载入…… → 祝您使用愉快！」，最后整层
 * 淡出，露出**已经在下面渲染好**的主界面。
 *
 * 为什么挂在根节点、用事件驱动：跳转一发生登录页就卸载了，跟着它走的遮罩会断在
 * 半路。这里自己管生命周期，跳转只是「换掉下面那层」。
 *
 * 为什么是圆而不是和按钮同形的胶囊：胶囊的 9999px 圆角在非等比放大后会切掉画面
 * 四角；圆的 50% 等比缩放永远不变形。所以圆点从按钮高度起步，等比放大到盖住四角。
 *
 * 性能：全程只动 transform 与 opacity，零布局、零重绘；纯色层放大会有伪影，
 * 纯色不会。不碰 blur / shadow / backdrop-filter。
 *
 * 时序（非 reduced-motion）：
 *   0 ─ 起幕（画布色，立即不透明）+ 紫点从按钮处放大
 *   420 ─ 圆已满屏，文字换成「验证成功！正在载入……」
 *   +780（且等主界面挂载）─ 文字换成「祝您使用愉快！」
 *   +640 ─ 整层淡出
 *   +320 ─ 卸载
 * 主界面先就绪也**不会**提前切换，一定等 rythm 走完；没就绪则等它，最多 8s。
 */
export const ENTER_TRANSITION_EVENT = 'enter-transition'
/** 主界面首帧画好之后由 Layout 派发 */
export const APP_READY_EVENT = 'app-ready'
/** 首屏数据也回来了（ChatArea 拉完群列表）——慢网时靠它把紫幕多挡一会儿，
 *  别让用户看到半成品。等不到就按 DATA_GRACE_MS 放行，不至于把人锁住。 */
export const APP_DATA_READY_EVENT = 'app-data-ready'

/** 圆点的最小直径（px）：按钮再矮也不至于成一个小点 */
const BASE_MIN = 40
/** 紫圆放大铺满屏幕 */
const EXPAND_MS = 420
/** 「验证成功！正在载入……」的最短停留：主界面秒开也要让这句话被读完 */
const LOADING_MIN_MS = 780
/** 「祝您使用愉快！」的停留 */
const WELCOME_HOLD_MS = 640
/** 整层淡出 */
const REVEAL_MS = 320
/** 主界面迟迟不挂载时的兜底（不让人永远卡在紫幕后面） */
const READY_TIMEOUT_MS = 8000
/** 首屏数据迟迟不回来时的兜底：宁可放行，也别让人对着紫幕干等 */
const DATA_GRACE_MS = 4000
/** 圆多盖 3%，避免四角因取整露出发丝缝 */
const COVER_PAD = 1.03

const REDUCE =
  typeof window !== 'undefined' &&
  !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

type Rect = { x: number; y: number; w: number; h: number }
type Variant = 'login' | 'register'
type Phase = 'idle' | 'expand' | 'loading' | 'welcome' | 'reveal'
type EnterDetail = { rect?: Rect; brand?: Rect; variant?: Variant }

export default function EnterTransition() {
  const t = useT()
  const [phase, setPhase] = useState<Phase>('idle')
  const [variant, setVariant] = useState<Variant>('login')
  const [rect, setRect] = useState<Rect | null>(null)
  // 品牌区在屏幕上的原位：过场里按这个坐标重画一份，接到登录页那一份上
  const [brand, setBrand] = useState<Rect | null>(null)
  // 每场过场抽一次的随机透明度（9 块）。必须在开局定死——
  // 放在渲染里抽的话每次重渲染都会变，画面会闪。
  const [alphas, setAlphas] = useState<number[]>([])

  // 代际：每起一次过场 +1，旧的定时器链看到代际变了就自己停手
  const genRef = useRef(0)
  // 主界面是否已经画好；没画好时把「该切到欢迎语了」这件事挂起
  const readyRef = useRef(false)
  const waitersRef = useRef<Array<() => void>>([])
  const timersRef = useRef<number[]>([])

  // 首屏数据就绪的回调：每场过场换一次，卸载时清空
  const onDataReadyRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const onReady = () => {
      readyRef.current = true
      const ws = waitersRef.current
      waitersRef.current = []
      ws.forEach((f) => f())
    }
    const onDataReady = () => onDataReadyRef.current?.()
    window.addEventListener(APP_READY_EVENT, onReady)
    window.addEventListener(APP_DATA_READY_EVENT, onDataReady)
    return () => {
      window.removeEventListener(APP_READY_EVENT, onReady)
      window.removeEventListener(APP_DATA_READY_EVENT, onDataReady)
    }
  }, [])

  useEffect(() => {
    const clearTimers = () => {
      timersRef.current.forEach((id) => window.clearTimeout(id))
      timersRef.current = []
    }
    const after = (ms: number, fn: () => void) => {
      timersRef.current.push(window.setTimeout(fn, ms))
    }
    const expandMs = REDUCE ? 0 : EXPAND_MS

    const onEnter = (e: Event) => {
      const detail = (e as CustomEvent<EnterDetail>).detail
      const r = detail?.rect
      // 量不到按钮位置就不演：宁可没有过场，也不能从屏幕中央冒出来
      if (!r) return

      clearTimers()
      waitersRef.current = []
      const gen = ++genRef.current
      const alive = () => gen === genRef.current

      setVariant(detail?.variant ?? 'login')
      setRect(r)
      setBrand(detail?.brand ?? null)
      // 9 块（左右各 3 + 底部 3），5%–22%：区间放宽，浓淡对比更明显
      setAlphas(Array.from({ length: 9 }, () => 0.05 + Math.random() * 0.17))
      setPhase('expand')

      let timeDone = false
      let dataDone = false
      let moved = false

      // 首屏数据可能比「正在载入」那句还早到（正常情况就是），所以从起幕就挂上
      onDataReadyRef.current = () => {
        if (!alive()) return
        dataDone = true
        toWelcome()
      }

      // 三个门同时开才换欢迎语：句子读完了 && 主界面画好了 && 首屏数据回来了。
      // 最后一门就是「阻塞」：网慢时数据没回来，紫幕就一直挡着，不让人看半成品。
      const toWelcome = () => {
        if (moved || !alive() || !timeDone || !dataDone || !readyRef.current) return
        moved = true
        setPhase('welcome')
        after(WELCOME_HOLD_MS, () => {
          if (!alive()) return
          setPhase('reveal')
          after(REVEAL_MS, () => {
            if (!alive()) return
            setPhase('idle')
            setRect(null)
            setBrand(null)
            onDataReadyRef.current = null
          })
        })
      }

      after(expandMs, () => {
        if (!alive()) return
        setPhase('loading')
        // 数据迟迟不回来也别把人锁死：到点就当它好了
        after(DATA_GRACE_MS, () => {
          dataDone = true
          toWelcome()
        })
        after(LOADING_MIN_MS, () => {
          timeDone = true
          toWelcome()
        })
      })

      if (!readyRef.current) waitersRef.current.push(toWelcome)
      // 事件丢了也别把人锁在紫幕里
      after(READY_TIMEOUT_MS, () => {
        readyRef.current = true
        toWelcome()
      })
    }

    window.addEventListener(ENTER_TRANSITION_EVENT, onEnter)
    return () => {
      window.removeEventListener(ENTER_TRANSITION_EVENT, onEnter)
      clearTimers()
      waitersRef.current = []
    }
  }, [])

  if (phase === 'idle' || !rect) return null

  const cx = rect.x + rect.w / 2
  const cy = rect.y + rect.h / 2
  // 圆从按钮高度起步：这一刻它整个藏在按钮里，看起来就是按钮本身在长大
  const base = Math.max(rect.h, BASE_MIN)
  // 放大到「圆心到最远那个角的距离」两倍，四角才都盖得住
  const far = Math.hypot(
    Math.max(cx, window.innerWidth - cx),
    Math.max(cy, window.innerHeight - cy),
  )
  const k = Math.max((far * 2 * COVER_PAD) / base, 1)

  // 灭点 = Copree 图标中心。所有楔形的延长线都交到这里，屏幕上才是同一个透视，
  // 而不是各自乱指。图标在品牌区顶部：品牌区顶 + 半个图标高（56px 的一半）。
  const W = window.innerWidth
  const H = window.innerHeight
  // 竖屏时 H 远大于 W，「横向尺寸」（伸入长度、底部块的宽度）按 H 算会冲出屏幕。
  // 统一拿短边当基准：横屏时 U === H，横屏观感与原来完全一致；竖屏时 U === W，自动收进来。
  // 纵向的厚度/高度仍按 H —— 那是竖直方向的量，本来就该跟屏高走。
  const U = Math.min(W, H)
  const vx = brand ? brand.x + brand.w / 2 : W / 2
  const vy = brand ? brand.y + 28 : H * 0.28

  // 每条从屏幕外的出口点出发，朝灭点走 reach 比例的距离（收在图标之前）
  // 建筑立面：竖边恒竖直（垂直于地面、不汇聚），横边才是「平行线」——
  // 它们的延长线都过灭点。所以只给根部两个点（贴在屏幕边缘），
  // 远端两个点由「过灭点的直线」算出来：k 是这条线在两处 x 上的比例。
  const faces = [
    // 左上：最大的一块
    { x0: 0, y1: 0.26 * H, y2: 0.60 * H, x1: vx * 0.42, a: 0.17 },
    // 左第二：夹在左上下面（间隔 3%），和左下之间留出 14% 的大空档——
    // 三段不再等距，左边才有疏密
    // 长度必须收在灭点之前：竖屏时 0.8U 会超过灭点 x，k 转负、形状翻转成一大块，
    // 盖住右上那块（手机上看着像两块连起来了）。clamp 到 0.88 个灭点距离之内。
    { x0: 0, y1: 0.63 * H, y2: 0.76 * H, x1: Math.min(0.76 * U, vx * 0.88), a: 0.11 },
    // 左第三：减高 = 缩短到 0.2H 左右（厚度保持原样 9%）
    { x0: 0, y1: 0.90 * H, y2: 0.99 * H, x1: 0.21 * U, a: 0.09 },
    // 右上：加高 = 从右边伸得更长（厚度保持原样 21%）
    { x0: W, y1: 0.19 * H, y2: 0.40 * H, x1: vx + (W - vx) * 0.30, a: 0.15 },
    // 右中：最长的一块，几乎切到画面中部
    { x0: W, y1: 0.62 * H, y2: 0.86 * H, x1: vx + (W - vx) * 0.18, a: 0.12 },
    // 右下：贴底的那块
    { x0: W, y1: 0.92 * H, y2: 1.00 * H, x1: vx + (W - vx) * 0.36, a: 0.08 },
  ].map((f) => {
    const k = (f.x1 - vx) / (f.x0 - vx)
    const y3 = vy + (f.y1 - vy) * k
    const y4 = vy + (f.y2 - vy) * k
    const left = Math.min(f.x0, f.x1)
    const top = Math.min(f.y1, f.y2, y3, y4)
    const px = (v: number) => v - left
    const py = (v: number) => v - top
    return {
      left,
      top,
      width: Math.abs(f.x1 - f.x0),
      height: Math.max(f.y1, f.y2, y3, y4) - top,
      vars: {
        '--x0': px(f.x0) + 'px',
        '--x1': px(f.x1) + 'px',
        '--y1': py(f.y1) + 'px',
        '--y2': py(f.y2) + 'px',
        '--y3': py(y3) + 'px',
        '--y4': py(y4) + 'px',
        '--shard-alpha': f.a,
      },
    }
  })

  // 从底边往上长的三块：和左右是同一套梯形，只是平行边换成水平的。
  // 底部两点贴在屏幕底边，顶部两点由「过灭点的射线」在 yTop 处算出，
  // 所以两侧边延长后都过 logo；宽度与左右同档（0.13–0.16H）。
  // 三块别一样高：左边那块明显挑高、也最粗；右边次之；中间留细的当呼吸。
  const floors = [
    // 底左这块最粗，压过侧边最粗的那条（侧边是 0.28H，这块 0.31H）
    { xa: 0.06 * W, w: 0.35 * U, yTop: 0.41 * H, a: 0.11 },
    { xa: 0.54 * W, w: 0.21 * U, yTop: 0.60 * H, a: 0.09 },
    { xa: 0.71 * W, w: 0.19 * U, yTop: 0.47 * H, a: 0.12 },
  ].map((f) => {
    const xb = f.xa + f.w
    const k = (f.yTop - vy) / (H - vy)
    const fx1 = vx + (f.xa - vx) * k
    const fx2 = vx + (xb - vx) * k
    const left = Math.min(f.xa, xb, fx1, fx2)
    const px = (v: number) => v - left
    return {
      left,
      top: f.yTop,
      width: Math.max(f.xa, xb, fx1, fx2) - left,
      height: H - f.yTop,
      vars: {
        '--fx1': px(fx1) + 'px',
        '--fx2': px(fx2) + 'px',
        '--bx1': px(f.xa) + 'px',
        '--bx2': px(xb) + 'px',
        '--shard-alpha': f.a,
      },
    }
  })

  // 三句的先后：当前这句从下方浮起，更早的继续往上走并淡出
  const lineIdx = phase === 'expand' ? 0 : phase === 'loading' ? 1 : 2
  const lineClass = (i: number) =>
    'enter-wipe-line' + (i === lineIdx ? ' is-on' : '') + (i < lineIdx ? ' is-past' : '')

  return createPortal(
    <div
      className={
        'enter-wipe fixed inset-0 z-max overflow-hidden'
        // 紫圆铺满之后才点亮：品牌区与文案在这一刻换成紫底专用的白字，
        // 之前对的是和登录页一样的浅底深字——交接那一帧颜色必须一模一样。
        + (phase === 'expand' ? '' : ' is-on-purple')
        + (phase === 'reveal' ? ' is-out' : '')
      }
      aria-hidden
    >
      {/* 底幕：一上来就不透明，把登录页和下面正在渲染的主界面一起挡住 */}
      <div className="enter-wipe-veil absolute inset-0" />

      {/* 紫圆：只做等比缩放 */}
      <div
        className="enter-wipe-disc absolute"
        style={{ left: cx, top: cy, width: base, height: base, '--wipe-k': String(k) } as CSSProperties}
      />

      {/* 暗角：只在紫圆铺满后淡入，给这整片紫一个中心和纵深（静态层，只动透明度） */}
      <div className="enter-wipe-tint absolute inset-0" />

      {/* 透视楔形：四条从左右边缘向中间伸的斜面。根部压在屏外，
          所以屏内看到的是梯形；生长与进度条共用同一条 780ms 时间轴 */}
      {faces.map((f, i) => (
        <span
          key={i}
          className="enter-wipe-shard"
          style={{
            left: f.left, top: f.top, width: f.width, height: f.height,
            ...f.vars, '--shard-alpha': alphas[i] ?? f.vars['--shard-alpha'],
          } as CSSProperties}
        />
      ))}
      {floors.map((f, i) => (
        <span
          key={'floor' + i}
          className="enter-wipe-shard enter-wipe-shard--floor"
          style={{
            left: f.left, top: f.top, width: f.width, height: f.height,
            ...f.vars, '--shard-alpha': alphas[6 + i] ?? f.vars['--shard-alpha'],
          } as CSSProperties}
        />
      ))}

      {/* 极淡噪点：把大面积纯色压成有纸感的材质，铺满后才出现 */}
      <div className="enter-wipe-grain absolute inset-0" />

      {/* 品牌区：浮在紫圆之上、位置与登录页像素对齐，一路留到最后的整层淡出 */}
      {brand && (
        <div
          className="enter-wipe-brand absolute"
          style={{ left: brand.x, top: brand.y, width: brand.w }}
        >
          <LoginBrand />
        </div>
      )}

      {/* 文字：锚死在按钮中心，三个阶段只换字不动位置 */}
      <div className="enter-wipe-label absolute" style={{ left: cx, top: cy }}>
        <span className={lineClass(0) + ' is-first'}>
          <Loader2 size={16} className="animate-spin" />
          {t('auth:verifying')}
        </span>
        <span className={lineClass(1)}>
          {t(variant === 'register' ? 'auth:registerSuccessLoading' : 'auth:verifySuccessLoading')}
        </span>
        <span className={lineClass(2)}>
          {t('auth:enterWelcome')}
        </span>
      </div>

      {/* 加载引导：通栏一条，从左拉到右；只在「正在载入」那句期间出现，
          走满就是该换欢迎语的时刻，之后收起 */}
      <span
        className={'enter-wipe-bar' + (phase === 'loading' ? ' is-on' : '')}
        style={{ top: cy + 30 }}
      >
        <span />
      </span>
    </div>,
    document.body,
  )
}
