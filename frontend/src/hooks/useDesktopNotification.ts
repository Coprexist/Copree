/**
 * 桌面通知：标签页标题未读计数 + 任务栏闪烁
 *
 * - 标签页失焦时，标题显示 "(N) Copree"
 * - 有新未读消息时自动交替标题，触发 Edge/Chrome 任务栏闪烁
 * - 标签页聚焦后立即清除闪烁，恢复原标题
 * - localStorage "notifications_enabled" 控制开关（默认开启）
 * - 免打扰（DND）群/私信不计入未读
 *
 * 未读数取自会话列表那一份（唯一来源），不再自己去拉 /groups + /dm/sessions：
 * 一条消息会同时惊动侧栏、这里、弹窗缓存三家，各自去拉就是三倍的请求
 * （实测这两个接口 146 条 SQL、1.35 秒）。
 *
 * 标题的写入口只有 utils/docTitle 一处：useWebSocket 收到消息时也要闪烁，
 * 两边各自 setInterval 会以 800ms/1000ms 互相覆盖（详见该模块注释）。
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { CHAT_REFRESH_EVENT } from '../constants'
import { useT } from '../i18n/I18nContext'
import { refreshChatLists, useChatLists } from './useChatLists'
import { setCountVisible, setUnreadCount, startTitleFlash, stopTitleFlash } from '../utils/docTitle'

const STORAGE_KEY = 'notifications_enabled'

/** 会话列表 → 总未读数：免打扰只挡常规消息，被点名到个人的那条照算 */
function countUnread(groups: any[], sessions: any[]): number {
  let total = 0
  for (const g of groups || []) {
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
  for (const s of sessions || []) {
    // 当前用户对这段私信的免打扰
    if (s.my_dnd_until) continue
    if (s.unread_count > 0) total += s.unread_count
  }
  return total
}

export function useDesktopNotification() {
  const [enabled, setEnabledState] = useState<boolean>(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    // 默认开启
    return stored === null ? true : stored === 'true'
  })
  const t = useT()
  const { groups, sessions } = useChatLists()
  const total = useMemo(() => countUnread(groups, sessions), [groups, sessions])
  const prevRef = useRef(0)

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value)
    localStorage.setItem(STORAGE_KEY, value ? 'true' : 'false')
    if (!value) {
      // 关闭通知时立即恢复标题
      setUnreadCount(0)
      stopTitleFlash()
    }
  }, [])

  // 未读数变了 → 写标题；失焦且有新增 = 启动闪烁（闪烁那一面由 docTitle 按当前 unread 画）
  useEffect(() => {
    const prev = prevRef.current
    prevRef.current = total
    // 关掉桌面通知时计数不进标题（unread 归零即回到原标题）
    setUnreadCount(enabled ? total : 0)
    if (enabled && total > 0 && total > prev && document.hidden) {
      startTitleFlash(t('chat:newMessages'))
    } else if (total === 0) {
      stopTitleFlash()
    }
  }, [total, enabled, t])

  // 窗口聚焦/失焦：计数该露就露、该收就收；顺带重拉一次列表，
  // 别的标签页/设备读过的未读靠这一次自愈
  useEffect(() => {
    const onFocus = () => {
      setCountVisible(false)
      stopTitleFlash()
      refreshChatLists()
    }
    const onBlur = () => {
      // 失焦时立即把计数亮出来（数字来自会话列表那一份）
      setCountVisible(true)
      refreshChatLists()
    }

    window.addEventListener('focus', onFocus)
    window.addEventListener('blur', onBlur)

    return () => {
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('blur', onBlur)
      setCountVisible(false)
      stopTitleFlash()
    }
  }, [])

  // 会话列表本身可能变了（自己发过消息、建了新会话）→ 重拉一次
  useEffect(() => {
    const handler = (e: Event) => {
      const type = (e as CustomEvent).detail?.type
      if (type === 'dm_notification' || type === 'unread_update' || type === 'message_sent') {
        refreshChatLists()
      }
    }
    window.addEventListener(CHAT_REFRESH_EVENT, handler)
    return () => window.removeEventListener(CHAT_REFRESH_EVENT, handler)
  }, [])

  // enabled 变化时刷新标题
  useEffect(() => {
    if (!enabled) {
      setUnreadCount(0)
      stopTitleFlash()
    }
  }, [enabled])

  return { enabled, setEnabled }
}
