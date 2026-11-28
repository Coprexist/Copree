/**
 * 备注（我给群 / 人起的称呼）的前端唯一来源
 *
 * 为什么要有 store：备注出现在会话列表、群设置、私信设置、资料卡四个地方，
 * 每处自己去 GET /user/remarks 就是「点一次头像多一次往返」。这里一次拉全量进内存，
 * 写入后就地更新并广播，四个消费者同步——形状与 useChatLists 同源（sync external store）。
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { api } from '../api/client'

export type RemarkTarget = 'group' | 'user'

/** 'group:12' / 'user:7' → 备注。没设过就没有这条键 */
const store = new Map<string, string>()
const listeners = new Set<() => void>()
let loaded = false
let inflight: Promise<void> | null = null

function keyOf(targetType: RemarkTarget, targetId: number): string {
  return targetType + ':' + targetId
}

function notify(): void {
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** 拉一次全量备注（同一时刻只发一次；已拉过就不再拉） */
function ensureLoaded(): Promise<void> {
  if (loaded) return Promise.resolve()
  if (inflight) return inflight
  inflight = api.get<{ remarks: Array<{ target_type: string; target_id: number; remark: string }> }>('/user/remarks')
    .then((data) => {
      store.clear()
      for (const row of data.remarks || []) {
        if (row.remark) store.set(keyOf(row.target_type as RemarkTarget, row.target_id), row.remark)
      }
      loaded = true
      notify()
    })
    .catch(() => { /* 拉不到就按「没有备注」渲染，不影响资料卡本身 */ })
    .finally(() => { inflight = null })
  return inflight
}

export interface UseRemark {
  /** 已保存的备注（空串 = 没设过）；同时是「保存按钮是否可点」的基线 */
  value: string
  saving: boolean
  save: (next: string) => Promise<void>
}

/** 一个对象的备注：读 store、写库后就地更新 */
export function useRemark(targetType: RemarkTarget, targetId: number): UseRemark {
  const value = useSyncExternalStore(
    subscribe,
    () => store.get(keyOf(targetType, targetId)) ?? '',
  )
  const [saving, setSaving] = useState(false)

  useEffect(() => { void ensureLoaded() }, [])

  const save = useCallback(async (next: string) => {
    setSaving(true)
    try {
      await api.put('/user/remarks/' + targetType + '/' + targetId, { remark: next })
      const body = next.trim()
      if (body) store.set(keyOf(targetType, targetId), body)
      else store.delete(keyOf(targetType, targetId))
      notify()
      // 会话列表优先显示备注：存完让它重拉一次（列表不读这个 store）
      window.dispatchEvent(new CustomEvent('groupListRefresh'))
    } finally {
      setSaving(false)
    }
  }, [targetType, targetId])

  return { value, saving, save }
}
