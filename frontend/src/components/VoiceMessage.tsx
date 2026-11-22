import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Pause, Play } from 'lucide-react'
import { fileDownloadUrl } from '../api/client'
// m:ss 的口径放在 utils/format 里只写一份：播放时间与存储页的语音时长共用
import { formatClock as clock } from '../utils/format'

/**
 * 语音消息播放条
 *
 * 数据源是 AnalyserNode 的实时频谱（按你的要求：不换成时间包络）。
 * 频谱天然低频强、高频弱，直接画就是"左边一坨"，所以：
 *   1. 先按名次 + 幅度做对比拉伸，把强弱铺开；
 *   2. **水平翻转**——把强段翻到右边；
 *   3. 传播取"右侧旧帧"的值，峰于是从右往左平移过去。
 *
 * 静止时显示按时间排的静态形状；播放中显示实时电平；暂停定格在最后一帧。
 */

const WIDTH = 240
const CANVAS_W = 138
const HEIGHT = 24
const F_MIN = 80
const F_MAX = 8000
const BINS_PER_BAR = 32
const FFT_MIN = 512
const FFT_MAX = 4096

// 相邻帧要保持高度相关（参考实现实测 0.84），所以平滑要慢：跟手但不抖动
const ATTACK = 0.55
const RELEASE = 0.06
// 入口那几格单独用更跟手的系数：比整体快一点，但仍有拖尾
const ENTRY_ATTACK = 0.85
const ENTRY_RELEASE = 0.35
const QUIET_MX = 18
const LOUD_MX = 72
const RANK_MIX = 0.55

// 传播两版并存，PROP_MODE 切换；默认 v1，v2 留着之后调。
const PROP_MODE: 'delay' | 'frames' = 'delay'
const PROP_FEED = 4                        // 每帧从频谱里取几格进左边（入口格同时接平移来的数据）
const PROP_SHIFT = 1                       // v2 用的格步长

const SETTLE_IN_MS = 320                   // 开始：静态形状 → 实时波形
const SETTLE_OUT_MS = 700                  // 结束：最后一帧 → 静态形状（慢一点，看得见收尾）
// 行波：几条低频正弦叠加、各自相位推进（参考 BREEZE 面板那条波浪）。
// 视觉上波峰从左往右流，但不是像素搬家。
const WAVE_FREQS = [2, 3, 5]               // 整条上的波峰数
const WAVE_AMPS = [0.55, 0.3, 0.15]        // 各分量幅度
const WAVE_SPEEDS = [2.6, 5.2, 7.8]        // 相位推进速度（Hz）
const WAVE_PHASES = [0, 1.7, 3.1]          // 初始相位
const WAVE_MIX = 0.75                      // 行波占多少，实时频谱占其余
// v1：单帧延迟线 —— 形状整体右移 1 格，长短关系原样带过去
const PROP_DECAY = 0.98                    // 每推进一格衰减（0.98^31≈0.53，走到右端还看得见）
// v2：多帧加权 —— 每格 = 前几帧各自左移 k 格的值按 PROP_FRAME_DECAY^k 加权平均
const PROP_FRAMES = 8
const PROP_FRAME_DECAY = 0.85
// v2 的权重每帧都一样，直接预算成常量，不在渲染循环里 pow
const WAVE_WEIGHTS = Array.from({ length: PROP_FRAMES }, (_, idx) => Math.pow(PROP_FRAME_DECAY, idx + 1))

function barsForDuration(seconds: number): number {
  if (!Number.isFinite(seconds) || seconds <= 0) return 32
  if (seconds < 3) return 16
  if (seconds > 120) return 48
  return 32
}

function barsOf(seed: number, count: number): number[] {
  let x = (seed || 1) >>> 0
  const out: number[] = []
  for (let i = 0; i < count; i += 1) {
    x ^= x << 13; x >>>= 0
    x ^= x >>> 17
    x ^= x << 5; x >>>= 0
    out.push(0.28 + (x / 4294967296) * 0.72)
  }
  return out
}

function cssVar(name: string, fallback: string): string {
  try {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    if (!raw) return fallback
    const parts = raw.split(/\s+/)
    return parts.length === 3 ? `rgb(${parts.join(', ')})` : fallback
  } catch {
    return fallback
  }
}

interface Props {
  /** 音频来源二选一：附件走 fileId，世界文件/预览弹窗里的直链走 src */
  fileId?: number
  src?: string
  name: string
  isMine: boolean
  /** 这条语音说了什么（AI 的语音由它自己的文字合成，文稿是现成的；人的语音要等 ASR） */
  transcript?: string
  /** 文稿是否展开：由消息菜单的「显示/隐藏文稿」控制 */
  showTranscript?: boolean
  /** 操作菜单（···）插槽：纯语音消息没有气泡依附，把菜单挂到时长后面 */
  menuSlot?: ReactNode
  /** 尺寸：聊天里 inline（240px 一行），预览弹窗里 large（更宽更高，一屏不该只剩一个小条） */
  size?: 'inline' | 'large'
}

export default function VoiceMessage({ fileId, src, name, isMine, transcript, showTranscript = true, menuSlot, size = 'inline' }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const timeRef = useRef<HTMLSpanElement | null>(null)
  const rafRef = useRef<number | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const bucketsRef = useRef<Array<[number, number]>>([])
  const levelsRef = useRef<number[]>([])     // 给 draw 用（已翻转 + 已传播）
  const smoothRef = useRef<number[]>([])     // Attack/Decay 状态（未翻转）
  const envHistRef = useRef<number[]>([])    // 响度延迟线（环形队列，O(1) 读写）
  const envPosRef = useRef(0)                // 环形队列的写指针
  const liveRef = useRef<number[]>([])       // 本帧真实采样值（不含过渡混合），避免过渡结果被再采样
  const targetsRef = useRef<number[]>([])    // 每帧复用的临时数组
  const shapeRef = useRef<number[]>([])      // 静态形状镜像，供过渡用
  const settleRef = useRef<{ from: number[]; start: number; to: 'live' | 'static' } | null>(null)
  const entrySmoothRef = useRef<number[]>([]) // 入口几格自己的平滑状态（比整体快一点）
  const historyRef = useRef<number[][]>([])  // 最近几帧（已翻转），用于平移
  const envRef = useRef(0)
  const draggingRef = useRef(false)
  const loopRef = useRef(0)                  // rAF 循环令牌：新循环一起，旧循环立刻失效

  // 尺寸只在这里分档：波形画多大、按钮多大、文稿几行，都从这一处派生
  const large = size === 'large'
  const canvasW = large ? 300 : CANVAS_W
  const canvasH = large ? 44 : HEIGHT
  const boxW = large ? 420 : WIDTH
  const initialBars = large ? 44 : 32

  const audioUrl = src ?? (fileId != null ? fileDownloadUrl(fileId) : '')
  // 静态波形的种子按文件身份取：同一个文件每次打开都是同一个形状，不同文件各不相同
  const seed = fileId ?? [...audioUrl].reduce((acc, ch) => (acc * 31 + ch.charCodeAt(0)) | 0, 7)

  const [playing, setPlaying] = useState(false)
  // 文稿默认只露两行（聊天列表不该被一段转写撑高），点一下展开全文、再点收回
  const [expanded, setExpanded] = useState(false)
  const [barCount, setBarCount] = useState(initialBars)
  const [shape, setShape] = useState<number[]>(() => barsOf(seed, initialBars))

  const accent = isMine ? 'rgba(255,255,255,0.95)' : cssVar('--tw-primary-400', 'rgb(139, 92, 246)')
  const track = isMine ? 'rgba(255,255,255,0.32)' : cssVar('--tw-border', 'rgba(128,128,128,0.35)')

  const draw = useCallback((progress: number, active: boolean, colorFade = 0, lineVisible = true, lineScale = 1) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = window.devicePixelRatio || 1
    if (canvas.width !== Math.round(canvasW * dpr) || canvas.height !== Math.round(canvasH * dpr)) {
      canvas.width = Math.round(canvasW * dpr)
      canvas.height = Math.round(canvasH * dpr)
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, canvasW, canvasH)

    const count = shape.length || barCount
    const step = canvasW / count
    const barW = Math.max(1.5, step * 0.55)
    const mid = canvasH / 2
    // 大尺寸（预览弹窗）用「上窄下宽、左边竖直、右边外扩」的锥形条——尺寸够大才看得出笔锋；
    // 聊天里一根只有 2px 多宽，锥形看不出来还会让边缘发毛，那里保持规整圆角条
    const taper = large ? 0.16 : 0

    // 先整条铺灰色底（一次 fill），已播放的部分再用强调色叠上去（一次 fill）。
    // 结束时用 globalAlpha 把紫色整段渐隐到灰色，而不是从右往左退。
    const addBar = (i: number) => {
      const level = active
        ? Math.max(0.08, levelsRef.current[i] || 0)
        : Math.max(0.08, shape[i] || 0.28)
      const h = Math.min(canvasH, Math.max(2, level * canvasH))
      const top = mid - h / 2
      const bottom = mid + h / 2
      const x = i * step + (step - barW) / 2
      const wTop = barW * (1 - taper / 2)
      const wBot = barW * (1 + taper / 2)
      // 造型：顶部小半圆、左边竖直、底部左半圆、右下尖角、右边缘外斜——就是原来那个「好看的样子」，
      // 只是半径由条宽按比例给出，不再写死：large 取顶宽 0.32 倍（小而利落，不是胶囊那两个大圆头），
      // inline（taper=0）取消锥度、半径取半宽，退化为规整的圆角细条，正好是聊天里要的那种。
      const r = Math.min(wTop * (large ? 0.32 : 0.5), h / 2)
      ctx.moveTo(x, top + r)
      ctx.arc(x + r, top + r, r, Math.PI, 0)      // 顶：半圆
      ctx.lineTo(x + wBot, bottom - r)            // 右：斜线直落（右下角保持尖）
      ctx.arc(x + r, bottom - r, r, 0, Math.PI)   // 底：左半圆
      ctx.closePath()                              // 左：竖直收口
    }

    ctx.fillStyle = track
    ctx.beginPath()
    for (let i = 0; i < count; i += 1) addBar(i)
    ctx.fill()

    const playedAlpha = 1 - colorFade
    if (playedAlpha > 0.01) {
      ctx.globalAlpha = playedAlpha
      ctx.fillStyle = accent
      ctx.beginPath()
      for (let i = 0; i < count; i += 1) {
        // 按条的"中心"跟进度线比：之前用右边缘 (i+1) 算，紫色总落后半格
        if ((i + 0.5) / count <= progress) addBar(i)
      }
      ctx.fill()
      // 进度线：开始从 0 长到满高，结束时从中间缩到 0，同时跟着整段淡出
      const lineH = canvasH * lineScale * playedAlpha
      if (lineVisible && progress > 0 && lineH > 0.5) {
        ctx.fillRect(Math.min(canvasW - 1, Math.max(0, progress * canvasW)), (canvasH - lineH) / 2, 1.5, lineH)
      }
      ctx.globalAlpha = 1
    }
  }, [accent, track, shape, barCount, canvasW, canvasH, large])

  const drawRef = useRef(draw)
  useEffect(() => { drawRef.current = draw }, [draw])

  // 静态形状镜像：开始/结束的过渡要拿它当终点
  useEffect(() => { shapeRef.current = shape }, [shape])

  const buildBuckets = useCallback((analyser: AnalyserNode, sampleRate: number, count: number) => {
    const bins = analyser.frequencyBinCount
    const nyquist = sampleRate / 2
    const top = Math.min(F_MAX, nyquist * 0.98)
    const out: Array<[number, number]> = []
    for (let i = 0; i < count; i += 1) {
      const lo = F_MIN * Math.pow(top / F_MIN, i / count)
      const hi = F_MIN * Math.pow(top / F_MIN, (i + 1) / count)
      const from = Math.max(0, Math.min(bins - 1, Math.floor((lo / nyquist) * bins)))
      const to = Math.max(from, Math.min(bins - 1, Math.ceil((hi / nyquist) * bins)))
      out.push([from, to])
    }
    bucketsRef.current = out
  }, [])

  const ensureAudioGraph = useCallback(() => {
    const el = audioRef.current
    if (!el || audioCtxRef.current) return
    try {
      const Ctor = window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctor) return
      const ctx = new Ctor()
      const src = ctx.createMediaElementSource(el)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      analyser.smoothingTimeConstant = 0.2
      src.connect(analyser)
      analyser.connect(ctx.destination)
      audioCtxRef.current = ctx
      analyserRef.current = analyser
      buildBuckets(analyser, ctx.sampleRate, barCount)
    } catch {
      /* 建不起来就只剩静态形状，播放本身不受影响 */
    }
  }, [buildBuckets, barCount])

  const sampleLevels = useCallback(() => {
    const analyser = analyserRef.current
    if (!analyser) return
    const count = barCount
    const wantFft = Math.min(FFT_MAX, Math.max(FFT_MIN, 2 ** Math.ceil(Math.log2(count * BINS_PER_BAR))))
    if (analyser.fftSize !== wantFft) {
      analyser.fftSize = wantFft
      buildBuckets(analyser, audioCtxRef.current?.sampleRate || 48000, count)
    }

    const bins = new Uint8Array(analyser.frequencyBinCount)
    analyser.getByteFrequencyData(bins)
    const buckets = bucketsRef.current.length === count
      ? bucketsRef.current
      : Array.from({ length: count }, (_, i) => {
        const per = Math.max(1, Math.floor(bins.length / count))
        return [i * per, (i + 1) * per - 1] as [number, number]
      })

    const raw = new Array<number>(count)
    let mn = 255
    let mx = 0
    for (let i = 0; i < count; i += 1) {
      const [from, to] = buckets[i]
      let sum = 0
      let n = 0
      for (let j = from; j <= to; j += 1) {
        sum += bins[j] || 0
        n += 1
      }
      const v = n ? sum / n : 0
      raw[i] = v
      if (v < mn) mn = v
      if (v > mx) mx = v
    }
    if (smoothRef.current.length !== count) smoothRef.current = new Array(count).fill(0)

    // 整体响度包络：没声音时它自己落到 0，收下去是连续的
    const loud = Math.min(1, Math.max(0, (mx - QUIET_MX) / (LOUD_MX - QUIET_MX)))
    const envPrev = envRef.current
    const env = envPrev + (loud - envPrev) * (loud > envPrev ? ATTACK : RELEASE)
    envRef.current = env

    // 只用幅度、不做名次映射：名次每帧重排一次，形状会整体跳变（这就是之前"突变"的来源）。
    // 参考实现相邻帧相关系数 0.84 —— 每根条独立、缓慢起伏，不是每帧换一张脸。
    const span = Math.max(8, mx - mn)
    const smooth = smoothRef.current
    if (entrySmoothRef.current.length !== count) entrySmoothRef.current = new Array(count).fill(0)
    const entrySmooth = entrySmoothRef.current
    // 原地更新上一帧的数组，不再每帧 new
    const cur = smooth
    const targets = targetsRef.current.length === count ? targetsRef.current : new Array<number>(count).fill(0)
    targetsRef.current = targets
    for (let i = 0; i < count; i += 1) {
      const amp = Math.min(1, Math.max(0, (raw[i] - mn) / span))
      const w = 0.7 + 0.3 * Math.sin((Math.PI * (i + 0.5)) / count)
      const target = amp * w * env
      targets[i] = target
      const prev = cur[i] || 0
      const next = prev + (target - prev) * (target > prev ? ATTACK : RELEASE)
      cur[i] = Number.isFinite(next) ? next : 0
    }
    // 入口格：按响度降序把 32 段均分成 PROP_FEED 份，每份取平均（不是按频率分组），再用 ENTRY_* 平滑。
    const feeds = Math.min(PROP_FEED, count)
    const byLoud = [...targets].sort((a, b) => b - a)
    const per = Math.ceil(count / feeds)
    for (let i = 0; i < feeds; i += 1) {
      const from = i * per
      const to = Math.min(count, from + per)
      let sum = 0
      for (let j = from; j < to; j += 1) sum += byLoud[j] || 0
      const tv = to > from ? sum / (to - from) : 0
      const ep = entrySmooth[i] || 0
      entrySmooth[i] = ep + (tv - ep) * (tv > ep ? ENTRY_ATTACK : ENTRY_RELEASE)
    }

    const hist = historyRef.current
    if (PROP_MODE === 'frames') {
      hist.unshift(cur.slice())
      if (hist.length > PROP_FRAMES + 1) hist.length = PROP_FRAMES + 1
    }

    // 复用同一块数组，避免每帧 new
    const out = liveRef.current.length === count ? liveRef.current : new Array<number>(count).fill(0)
    liveRef.current = out

    if (PROP_MODE === 'delay') {
      // v1：三条低频正弦叠加的行波（相位各自推进）＋ 一点实时频谱。
      const now = performance.now() / 1000
      // 响度延迟线：格 i 乘 i 帧前的响度，声音一停是左边先暗、右边一排排滞后。
      // 环形队列：写入指针后移，取第 i 帧前的值直接算下标，不搬数组
      const envHist = envHistRef.current
      if (envHist.length !== count) {
        envHistRef.current = new Array<number>(count).fill(env)
        envPosRef.current = 0
      }
      const ring = envHistRef.current
      const pos = envPosRef.current
      ring[pos] = env
      envPosRef.current = (pos + 1) % count
      for (let i = 0; i < count; i += 1) {
        const x = i / count
        let v = 0
        for (let k = 0; k < WAVE_FREQS.length; k += 1) {
          v += WAVE_AMPS[k] * Math.sin(2 * Math.PI * WAVE_FREQS[k] * x - 2 * Math.PI * WAVE_SPEEDS[k] * now + WAVE_PHASES[k])
        }
        const wave = Math.min(1, Math.max(0, (v + 1) / 2))
        const envAt = ring[(pos - i + count) % count]
        out[i] = WAVE_MIX * wave * envAt + (1 - WAVE_MIX) * (cur[i] || 0)
      }
    } else {
      // v2：前 PROP_FRAMES 帧各往左推 k*PROP_SHIFT 格，按常量权重加权平均。
      for (let i = 0; i < count; i += 1) {
        if (hist.length < 2) {
          out[i] = cur[i]
          continue
        }
        let acc = 0
        let wsum = 0
        for (let k = 1; k <= PROP_FRAMES && k < hist.length; k += 1) {
          const src = ((i - k * PROP_SHIFT) % count + count) % count
          acc += (hist[k][src] || 0) * WAVE_WEIGHTS[k - 1]
          wsum += WAVE_WEIGHTS[k - 1]
        }
        out[i] = wsum > 0 ? acc / wsum : cur[i]
      }
    }
  }, [barCount, buildBuckets])

  const tick = useCallback(() => {
    const el = audioRef.current
    if (!el) return
    const duration = Number.isFinite(el.duration) ? el.duration : 0
    const progress = duration > 0 ? Math.min(1, el.currentTime / duration) : 0
    if (!el.paused) sampleLevels()

    const count = barCount
    const settle = settleRef.current
    let drawProgress = progress
    let drawFade = 0
    let drawLineScale = 1
    if (settle) {
      const settleMs = settle.to === 'live' ? SETTLE_IN_MS : SETTLE_OUT_MS
      const p = Math.min(1, (performance.now() - settle.start) / settleMs)
      const eased = 1 - Math.pow(1 - p, 3)
      const target = settle.to === 'live'
        ? (liveRef.current.length === count ? liveRef.current : settle.from)
        : shapeRef.current
      const mixed = new Array<number>(count)
      for (let i = 0; i < count; i += 1) {
        const a = settle.from[i] ?? 0
        const b = target[i] ?? 0
        mixed[i] = a + (b - a) * eased
      }
      levelsRef.current = mixed
      // 结束时：进度线淡出、整段紫色同时渐隐成灰色；开始时进度线从 0 长起来
      if (settle.to === 'static') {
        drawProgress = 1
        drawFade = eased
      } else {
        drawLineScale = eased
      }
      if (p >= 1) {
        settleRef.current = null
        if (settle.to === 'static') {
          levelsRef.current = []
          liveRef.current = []
          smoothRef.current = []
          envHistRef.current = []
          envPosRef.current = 0
          entrySmoothRef.current = []
          envRef.current = 0
          const audio = audioRef.current
          if (audio) audio.currentTime = 0
          drawRef.current(0, false)
          loopRef.current += 1
          if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
          rafRef.current = null
          setPlaying(false)
          return
        }
      }
    } else {
      levelsRef.current = liveRef.current
    }

    // 暂停后 paused 立刻为 true，但实时层还在：继续用它画，才能定格在暂停那一刻。
    // 只看 progress > 0：播放器 currentTime 恰好等于 duration 的那一帧 progress 正好是 1，
    // 之前的 "progress < 1" 会把它挡掉，导致进度线凭空消失一帧。
    const lineVisible = drawProgress > 0
    draw(drawProgress, !el.paused || levelsRef.current.length === count, drawFade, lineVisible, drawLineScale)
    if (timeRef.current) {
      timeRef.current.textContent = clock(el.currentTime > 0 ? el.currentTime : duration)
    }
  }, [draw, sampleLevels, barCount])

  // 唯一的循环入口：带令牌，避免 onPlay/onEnded 各起一个循环导致一帧画两次
  const startLoop = useCallback(() => {
    loopRef.current += 1
    const token = loopRef.current
    const frame = () => {
      if (loopRef.current !== token) return
      tick()
      if (loopRef.current === token) rafRef.current = requestAnimationFrame(frame)
    }
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(frame)
  }, [tick])

  useEffect(() => {
    setPlaying(false)
    setBarCount(initialBars)
    setShape(barsOf(seed, initialBars))
    settleRef.current = null
    envHistRef.current = []
    envPosRef.current = 0
    liveRef.current = []
    entrySmoothRef.current = []
    levelsRef.current = []
    smoothRef.current = []
    historyRef.current = []
    envRef.current = 0
    drawRef.current(0, false)
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    // 只认身份（fileId / src）：draw 随形状变，放进依赖会打断播放
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed])

  useEffect(() => () => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    try { void audioCtxRef.current?.close() } catch { /* ignore */ }
    audioCtxRef.current = null
    analyserRef.current = null
  }, [])

  const toggle = useCallback(() => {
    const el = audioRef.current
    if (!el) return
    ensureAudioGraph()
    void audioCtxRef.current?.resume().catch(() => undefined)
    if (el.paused) void el.play().catch(() => undefined)
    else el.pause()
  }, [ensureAudioGraph])

  const onPlay = useCallback(() => {
    setPlaying(true)
    // 开始：从静态形状平滑过渡到实时波形
    settleRef.current = {
      from: (shapeRef.current.length ? shapeRef.current : barsOf(seed, barCount)).slice(),
      start: performance.now(),
      to: 'live',
    }
    startLoop()
  }, [startLoop, seed, barCount])

  const onStop = useCallback(() => {
    // 播完时浏览器还会补一个 pause 事件，别让它打断结束动画
    if (settleRef.current?.to === 'static') return
    loopRef.current += 1
    settleRef.current = null
    setPlaying(false)
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    // 定格在暂停那一刻：canvas 上就是最后一帧，不重绘静态形状
  }, [])

  const seekFromEvent = useCallback((clientX: number) => {
    const el = audioRef.current
    const canvas = canvasRef.current
    if (!el || !canvas || !Number.isFinite(el.duration) || el.duration <= 0) return
    const rect = canvas.getBoundingClientRect()
    if (!rect.width) return
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
    el.currentTime = Math.min(el.duration - 0.04, ratio * el.duration)
    draw(ratio, !el.paused || levelsRef.current.length > 0)
    if (timeRef.current) timeRef.current.textContent = clock(el.currentTime)
  }, [draw])

  return (
    // 文稿在波形下面：语音条本身保持 240px 宽的一行，文字换行不挤压控制按钮
    <div className={`flex flex-col shrink-0 ${large ? 'gap-2' : 'gap-1'}`} style={{ width: boxW }}>
      <div className="flex items-center gap-2">
      <audio
        ref={audioRef}
        src={audioUrl}
        preload="metadata"
        onPlay={onPlay}
        onPause={onStop}
        onEnded={() => {
          // currentTime 先不归零：过渡期间它一归零，progress 会瞬间跳 0，画面闪一下
          // 播完：从最后一帧平滑落到静态形状，落完再回默认（清理逻辑在 tick 里）
          setPlaying(false)
          settleRef.current = {
            from: (liveRef.current.length ? liveRef.current : shapeRef.current).slice(),
            start: performance.now(),
            to: 'static',
          }
          startLoop()
        }}
        onLoadedMetadata={(e) => {
          const el = e.currentTarget
          const next = barsForDuration(el.duration)
          setBarCount(next)
          setShape(barsOf(seed, next))
          entrySmoothRef.current = []
          levelsRef.current = []
          smoothRef.current = []
          historyRef.current = []
          drawRef.current(0, false)
          if (timeRef.current) timeRef.current.textContent = clock(el.duration)
        }}
      />
      <button
        type="button"
        onClick={toggle}
        aria-label={playing ? '暂停' : '播放'}
        title={name}
        className={`shrink-0 ${large ? 'w-10 h-10' : 'w-7 h-7'} rounded-full flex items-center justify-center transition-colors ${isMine ? 'bg-white/20 hover:bg-white/30 text-white' : 'bg-primary-500/15 hover:bg-primary-500/25 text-primary-400'}`}
      >
        <span className="w-full h-full grid place-items-center">
          {playing ? <Pause size={large ? 18 : 13} className="block" /> : <Play size={large ? 18 : 13} className="block" />}
        </span>
      </button>
      <canvas
        ref={canvasRef}
        className={`${large ? 'h-11' : 'h-6'} cursor-pointer touch-none min-w-0 shrink-0`}
        style={{ width: canvasW, height: canvasH }}
        title="拖动可以跳转"
        onPointerDown={(e) => {
          draggingRef.current = true
          e.currentTarget.setPointerCapture(e.pointerId)
          seekFromEvent(e.clientX)
        }}
        onPointerMove={(e) => {
          if (draggingRef.current) seekFromEvent(e.clientX)
        }}
        onPointerUp={(e) => {
          draggingRef.current = false
          try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* ignore */ }
        }}
        onPointerCancel={() => { draggingRef.current = false }}
      />
      <span ref={timeRef} className={`shrink-0 ${large ? 'text-xs' : 'text-3xs'} tabular-nums ${isMine ? 'text-white/90' : 'text-textMuted'}`}>
        {'0:00'}
      </span>
      {/* 操作菜单（···）挂在时长之后：下载也收进这个菜单，行尾不再单独放一个下载按钮 */}
      {menuSlot}
      </div>
      {/* 文稿展开/收起走 height + opacity 的过渡：外层用 0fr→1fr 的 grid 行高，
          不用量文本高度，也不用 JS；prefers-reduced-motion 下直接不animate */}
      {transcript ? (
        <div
          className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none ${
            showTranscript ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
          }`}
        >
          <p
            onClick={() => { if (!large) setExpanded((v) => !v) }}
            title={large ? undefined : expanded ? '点一下收起' : '点一下展开全文'}
            className={
              'overflow-hidden leading-relaxed select-text ' +
              (large ? 'text-sm' : 'text-2xs cursor-pointer ' + (expanded ? '' : 'line-clamp-2 ')) +
              (isMine ? 'text-white/70' : 'text-textMuted')
            }
          >
            {transcript}
          </p>
        </div>
      ) : null}
    </div>
  )
}
