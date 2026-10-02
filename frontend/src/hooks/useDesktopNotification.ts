/**
 * 桌面通知：标签页标题未读计数 + 任务栏闪烁
 *
 * - 标签页失焦时，标题显示 "(N) Copree"
 * - 有新未读消息时自动交替标题，触发 Edge/Chrome 任务栏闪烁
 * - 标签页聚焦后立即清除闪烁，恢复原标题
 * - localStorage "notifications_enabled" 控制开关（默认开启）
 * - 免打扰（DND）群/私信不计入未读
 *
 * 标题的写入口只有 utils/docTitle 一处：useWebSocket 收到消息时也要闪烁，
 * 两边各自 setInterval 会以 800ms/1000ms 互相覆盖（详见该模块注释）。
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { CHAT_REFRESH_EVENT } from '../constants'
import { useT } from '../i18n/I18nContext'
import { setCountVisible, setUnreadCount, startTitleFlash, stopTitleFlash } from '../utils/docTitle'

const STORAGE_KEY = 'notifications_enabled'

/** 从 groups + dm_sessions API 计算总未读数：免打扰只挡常规消息，被点名到个人的那条照算 */
async function fetchTotalUnread(): Promise<number> {
  try {
    const [groups, dmSessions] = await Promise.all([
      api.get<any[]>('/groups'),
      api.get<any[]>('/dm/sessions'),
    ])
    let total = 0
    if (Array.isArray(groups)) {
      for (const g of groups) {
        if (g.dnd_until) {
          // 免打扰群不整段跳过：被点名时记 1（语义是"有一处找你"，不是这个群的未读数，
          // 所以不把几十条闲聊一起算进来）。这是**有意的产品口径**——@ 穿透免打扰、
          // @all 不穿透，与站内浮窗一致；看到"免打扰群怎么还会显示 1"别当成 bug 改掉。
          // 依据：后端 has_mention 只认点名到个人的令牌/名字，@all 不算。
          if (g.has_mention) total += 1
          continue
        }
        if (g.unread_count > 0) total += g.unread_count
      }
    }
    if (Array.isArray(dmSessions)) {
      for (const s of dmSessions) {
        // 当前用户对这段私信的免打扰
        if (s.my_dnd_until) continue
        if (s.unread_count > 0) total += s.unread_count
      }
    }
    return total
  } catch {
    return 0
  }
}

export function useDesktopNotification() {
  const [enabled, setEnabledState] = useState<boolean>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    // 默认开启
    return stored === null ? true : stored === 'true'
  })
  const t = useT()
  const unreadRef = useRef(0)

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value)
    localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false')
    if (!value) {
      // 关闭通知时立即恢复标题
      setUnreadCount(0)
      stopTitleFlash()
    }
  }, [])

  const updateUnread = useCallback(async () => {
    const count = await fetchTotalUnread()
    const prev = unreadRef.current
    unreadRef.current = count
    // 关掉桌面通知时计数不进标题（unread 归零即回到原标题）
    setUnreadCount(enabled ? count : 0)

    // 有新未读消息 + 标签页失焦 = 启动闪烁；计数那一面由 docTitle 按当前 unread 画
    if (count > 0 && count > prev && document.hidden && enabled) {
      startTitleFlash(t('chat.newMessages'))
    } else if (count === 0) {
      stopTitleFlash()
    }
  }, [enabled, t])

  // 窗口聚焦时清除闪烁和未读标记
  useEffect(() => {
    const onFocus = () => {
      setCountVisible(false)
      stopTitleFlash()
      // 聚焦后刷新一次未读计数
      fetchTotalUnread().then((count) => { unreadRef.current = count })
    }
    const onBlur = () => {
      // 失焦时立即刷新：窗口不在前台就把计数亮出来
      setCountVisible(true)
      fetchTotalUnread().then((count) => {
        unreadRef.current = count
        setUnreadCount(enabled ? count : 0)
      })
    }

    window.addEventListener('focus', onFocus)
    window.addEventListener('blur', onBlur)

    return () => {
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('blur', onBlur)
      setCountVisible(false)
      stopTitleFlash()
    }
  }, [enabled])

  // 监听 chat-refresh 事件更新未读
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      const t = detail?.type
      if (t === 'dm_notification' || t === 'unread_update' || t === 'message_sent') {
        updateUnread()
      }
    }
    window.addEventListener(CHAT_REFRESH_EVENT, handler)
    return () => window.removeEventListener(CHAT_REFRESH_EVENT, handler)
  }, [updateUnread])

  // enabled 变化时刷新标题
  useEffect(() => {
    if (!enabled) {
      setUnreadCount(0)
      stopTitleFlash()
    }
  }, [enabled])

  return { enabled, setEnabled }
}
