/**
 * 「此刻正在读哪个会话」的唯一真相
 *
 * 未读的语义是"有人说话了、而我没在看"。人贴在会话底部、标签页也看得见时，
 * 那一条就是在读：侧边栏据此**不画**红数泡（是判定，不是等后端回传再抹掉——
 * 后者在贴底时会闪一下），ChatView 据此把已读同步回后端。
 *
 * 为什么是模块级 store 而不是 context/事件：读它的 ChatSidebar 与写它的 ChatView 是兄弟，
 * 而事件只能表达"变了"，表达不了"此刻是不是"；后者必须能在渲染时同步读到。
 */
import { useSyncExternalStore } from 'react'

/** 会话身份：`group:39` / `dm:12_106`（跨群聊与私信唯一） */
export type ConversationKey = string

export function conversationKey(type: 'group' | 'dm', id: number | string): ConversationKey {
  return `${type}:${id}`
}

let following: ConversationKey | null = null
let visible = true
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => fn())
}

/** ChatView 上报"我在哪个会话的底部"；不在底部或离开会话时传 null */
export function setFollowingConversation(key: ConversationKey | null): void {
  if (following === key) return
  following = key
  emit()
}

/** 此刻正在读的会话。切到后台就不算：那几条本来就该记成未读（与桌面通知同一口径）。
 *  非组件代码（如收到推送时就地改会话列表）要判断「这条算不算已读」，用它 */
export function readingKey(): ConversationKey | null {
  return visible ? following : null
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** 订阅版（侧边栏、ChatView 用）；返回值稳定，可在依赖数组里比较 */
export function useReadingKey(): ConversationKey | null {
  return useSyncExternalStore(subscribe, readingKey)
}

if (typeof document !== 'undefined') {
  visible = document.visibilityState === 'visible'
  document.addEventListener('visibilitychange', () => {
    const next = document.visibilityState === 'visible'
    if (next === visible) return
    visible = next
    emit()
  })
}
