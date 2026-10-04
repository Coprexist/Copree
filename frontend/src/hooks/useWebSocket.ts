import { useEffect, useRef, useState, useCallback } from 'react'
import { getWsUrl } from '../utils/platform'
import { safeParse } from '../utils/result'
import { useT } from '../i18n/I18nContext'
import { BASE_TITLE, startTitleFlash, stopTitleFlash } from '../utils/docTitle'

export interface WebSocketMessage {
  type: string
  data?: any
  code?: string
  message?: string
  tool_call_id?: string
  conversation_type?: string
}

interface WsError {
  code: string
  message: string
  tool_call_id?: string
  timestamp: number
}

/** 重连参数 */
const RECONNECT_BASE_MS = 1000      // 首次重连等待 1s
const RECONNECT_MAX_MS = 30_000     // 最大 30s
const RECONNECT_MULTIPLIER = 2      // 每次翻倍

/** 计算重连延迟：指数退避 + ±30% 抖动 */
function calcReconnectDelay(retryCount: number): number {
  const base = Math.min(
    RECONNECT_BASE_MS * Math.pow(RECONNECT_MULTIPLIER, retryCount),
    RECONNECT_MAX_MS,
  )
  const jitter = base * 0.3 * (Math.random() * 2 - 1)
  return Math.round(base + jitter)
}

export function useWebSocket(
  conversationType: 'group' | 'dm',
  conversationId: number | string | null,
  opts?: { onMessage?: (msg: WebSocketMessage) => void },
) {
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const [errors, setErrors] = useState<WsError[]>([])
  const t = useT()
  const wsRef = useRef<WebSocket | null>(null)
  // 错误 toast 的自动消失定时器：卸载时统一清掉，别把 setState 落在已卸载的组件上
  const errorTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set())

  // 消息回调 ref — 由消费者设置，WebSocket onmessage 时调用
  // 用 ref 而非直接依赖，避免 connect useCallback 随回调变化而重建
  const onMessageRef = useRef<((msg: WebSocketMessage) => void) | undefined>(opts?.onMessage)
  onMessageRef.current = opts?.onMessage

  // 重连控制 ref
  const retryCountRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const flashLockRef = useRef(false)
  // connect 的 ref 间接引用：scheduleReconnect 与 connect 互相调用，用 ref 打破 useCallback 依赖环
  const connectRef = useRef<() => void>(() => {})

  // ── 调度重连（不直接依赖 connect，走 connectRef，避免循环依赖 + TDZ）──
  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current) return

    // 取消已有重连定时器
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }

    setReconnecting(true)
    retryCountRef.current += 1

    // retryCount 上面刚自增过：减 1 才对应「第 1 次重连等 1s」，否则首次就是 2s
    const delay = calcReconnectDelay(retryCountRef.current - 1)
    console.log(
      `🔌 WebSocket 将在 ${(delay / 1000).toFixed(1)}s 后重连（第 ${retryCountRef.current} 次）`,
    )

    retryTimerRef.current = setTimeout(() => {
      if (!mountedRef.current) return
      connectRef.current()
      // 如果 connect 同步失败（如 token 丢失），清除重连状态
      if (!wsRef.current) setReconnecting(false)
    }, delay)
  }, [])

  /** 建立 WebSocket 连接，返回清理函数 */
  const connect = useCallback(() => {
    const token = localStorage.getItem('access_token')
    if (!token) return () => {}

    // URL 构造收口 getWsUrl（内部 encodeURIComponent token——JWT 特殊字符 + Safari 严格解析 = 首登灰按钮根因）
    const url = getWsUrl(token)

    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch (e) {
      // URL 非法等同步异常：不崩溃，统一走重连调度（否则 wsRef 为 null 永不重试，按钮一直灰）
      console.error('⚠️ WebSocket 创建失败，稍后重试:', e)
      setConnected(false)
      scheduleReconnect()
      return () => {}
    }
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) {
        // 组件已卸载，不在此处 close（交由 useEffect cleanup 处理），避免浏览器报 "closed before established"
        return
      }
      setConnected(true)
      setReconnecting(false)
      retryCountRef.current = 0

      // 订阅当前对话
      if (conversationType === 'group') {
        ws.send(JSON.stringify({ type: 'subscribe', group_id: conversationId }))
      } else if (conversationId) {
        ws.send(JSON.stringify({ type: 'subscribe', session_id: conversationId }))
      }
    }

    ws.onmessage = (event) => {
      const result = safeParse<WebSocketMessage>(event.data)
      if (!result.ok) {
        console.warn('WebSocket 收到无效 JSON:', (event.data as string).slice(0, 100))
        return
      }
      const msg = result.value

      // 错误事件：自动消失的 toast
      if (msg.type === 'error') {
        const wsError: WsError = {
          code: msg.code || 'UNKNOWN',
          message: msg.message || 'Unknown error',
          tool_call_id: msg.tool_call_id,
          timestamp: Date.now(),
        }
        setErrors((prev) => [...prev.slice(-9), wsError])
        const timer = setTimeout(() => {
          errorTimersRef.current.delete(timer)
          setErrors((prev) => prev.filter((e) => e.timestamp !== wsError.timestamp))
        }, 5000)
        errorTimersRef.current.add(timer)
      }

      // v0.1.8: 余额弹窗 → 全局自定义事件（BalancePromptModal 监听）
      if (msg.type === 'balance_prompt' && msg.data) {
        window.dispatchEvent(new CustomEvent('balance-prompt', { detail: msg.data }))
      }

      // 插件变更广播（管理员开关/重扫）→ 全局事件（AuthContext 监听，皮肤即时回退）
      if (msg.type === 'plugins_changed') {
        window.dispatchEvent(new CustomEvent('plugins-changed'))
      }

      // 心跳 ping → 立即回复 pong
      if (msg.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong' }))
        return
      }

      // 只对真正的用户消息触发闪烁/通知，忽略静默系统消息
      const isUserMsg = msg.type === 'message' || msg.type === 'ai_response'
      if (!isUserMsg) {
        // 静默消息仍要分发给消费者（ChatView 处理订阅确认等）
        onMessageRef.current?.(msg)
        return
      }

      // 窗口闪烁：页面未聚焦时收到消息。标题只由 utils/docTitle 写——
      // 这里只报「有新消息、闪烁文案是什么」，自己开 interval 会和未读计数互相覆盖
      if (document.hidden && !flashLockRef.current && localStorage.getItem('notifications_enabled') !== 'false') {
        flashLockRef.current = true
        const origFavicon = ((document.querySelector('link[rel*="icon"]') || document.querySelector('link[rel="shortcut icon"]')) as HTMLLinkElement | null)?.href

        // 闪烁文案（带发送者昵称）：标题格式由 docTitle 统一拼
        const sender = msg.data?.sender_name || ''
        startTitleFlash(sender ? `${sender} · ${t('chat:newMessages')}` : t('chat:newMessages'))

        // Favicon 红点
        try {
          const link = document.querySelector<HTMLLinkElement>('link[rel*="icon"]') || document.querySelector<HTMLLinkElement>('link[rel="shortcut icon"]')
          if (link && origFavicon) badgeFavicon(origFavicon).then((url) => {
            // 用户已切回来则跳过（Promise 可能比 stop 慢）
            if (flashLockRef.current) link!.href = url
          })
        } catch {}

        // 桌面通知
        if ('Notification' in window && Notification.permission === 'granted') {
          try {
            new Notification(BASE_TITLE, { body: t('chat:newMessages'), tag: 'copree_msg' })
          } catch {}
        } else if ('Notification' in window && Notification.permission !== 'denied') {
          Notification.requestPermission()
        }

        const stop = () => {
          stopTitleFlash()
          if (origFavicon) {
            const link = document.querySelector<HTMLLinkElement>('link[rel*="icon"]') || document.querySelector<HTMLLinkElement>('link[rel="shortcut icon"]')
            if (link) link.href = origFavicon
          }
          flashLockRef.current = false
          window.removeEventListener('focus', stop)
          document.removeEventListener('visibilitychange', stop)
        }
        window.addEventListener('focus', stop, { once: true })
        document.addEventListener('visibilitychange', stop, { once: true })
      }

      // 分发给消费者回调（ChatView 注册）
      // 无需 flushSync：消费者内部全部使用函数式 setState(prev => ...),
      // 即使 React 18 批处理合并多次调用，prev 链式叠加也不会丢失消息。
      onMessageRef.current?.(msg)
    }

    ws.onclose = (event) => {
      // 1000=正常关闭, 1001=离开页面, 4001=认证失败 → 不重连
      const cleanClose = event.code === 1000 || event.code === 1001 || event.code === 4001
      if (!mountedRef.current || cleanClose) {
        setConnected(false)
        setReconnecting(false)
        return
      }
      // 非正常关闭 → 调度重连
      setConnected(false)
      scheduleReconnect()
    }

    ws.onerror = () => {
      // onclose 会紧接着触发，连接状态由 onclose 统一处理
    }

    return () => {
      // 清理：干净关闭，不触发重连
      ws.onclose = null
      ws.close(1000)
      wsRef.current = null
    }
  }, [conversationType, conversationId])

  // connect 挂到 ref（scheduleReconnect 通过 connectRef 调用，打破循环依赖）
  connectRef.current = connect

  // ── 主 effect：对话参数变化时重连 ──
  useEffect(() => {
    mountedRef.current = true

    // 对话切换 → 重置状态
    clearRetryTimer()
    retryCountRef.current = 0
    setConnected(false)
    setReconnecting(false)

    /** 切对话/卸载的收尾：定时器、闪烁、连接都要停 */
    const teardown = () => {
      mountedRef.current = false
      clearRetryTimer()
      errorTimersRef.current.forEach(clearTimeout)
      errorTimersRef.current.clear()
      // 闪烁不停会一直在后台改标题（原先那条 interval 就是这么漏的）
      stopTitleFlash()
    }

    if (!conversationId) return teardown

    const token = localStorage.getItem('access_token')
    if (!token) return teardown

    const cleanup = connect()

    return () => {
      teardown()
      if (cleanup) cleanup()
    }
  }, [conversationType, conversationId, connect])

  const sendMessage = useCallback((content: string, replyTo?: number, attachments?: Array<{file_id: number, name: string, size: number, mime_type: string}>) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const payload: any = {
        type: 'send',
        content,
        reply_to: replyTo ?? null,
      }
      if (attachments && attachments.length > 0) {
        payload.attachments = attachments
      }
      if (conversationType === 'group') {
        payload.group_id = conversationId
      } else {
        payload.session_id = conversationId
      }
      wsRef.current.send(JSON.stringify(payload))
    }
  }, [conversationType, conversationId])

  const sendTyping = useCallback((isTyping: boolean) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const payload: any = {
        type: 'typing',
        is_typing: isTyping,
      }
      if (conversationType === 'group') {
        payload.group_id = conversationId
      } else {
        payload.session_id = conversationId
      }
      wsRef.current.send(JSON.stringify(payload))
    }
  }, [conversationType, conversationId])

  const clearErrors = useCallback(() => setErrors([]), [])

  // 内联工具函数（仅操作 ref，不需要 useCallback）
  function clearRetryTimer() {
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }

  return {
    connected,
    reconnecting,
    errors,
    sendMessage,
    sendTyping,
    clearErrors,
  }
}

/** 在 favicon 右上角叠加红点，返回 data URL */
function badgeFavicon(src: string): Promise<string> {
  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const c = document.createElement('canvas')
        c.width = Math.max(img.width, 1)
        c.height = Math.max(img.height, 1)
        const ctx = c.getContext('2d')!
        ctx.drawImage(img, 0, 0)
        const r = Math.max(c.width, c.height) * 0.22
        const cx = c.width - r
        const cy = r
        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.fillStyle = '#ff3b30'
        ctx.fill()
        ctx.strokeStyle = '#fff'
        ctx.lineWidth = Math.max(1.5, r * 0.25)
        ctx.stroke()
        resolve(c.toDataURL('image/png'))
      } catch {
        resolve(src)
      }
    }
    img.onerror = () => resolve(src)
    img.src = src
  })
}
