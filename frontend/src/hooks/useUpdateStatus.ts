/**
 * 版本更新状态（来源：\`/api/system/update-status\`）。
 *
 * 为什么不直接问 GitHub：出站请求由后端统一缓存（TTL/单飞/节流），前端只读本地接口。
 * 让浏览器直连的话，境内网络下会变成「同一个人有时看得到更新、有时看不到」。
 *
 * 缓存策略与 useAppVersion 一致：模块级缓存 + 共享在飞请求，多个组件只发一次；
 * 失败不抛错也不重试——这是一个展示性功能，绝不能因为上游不通而影响主流程。
 * \`enabled=false\` 时一个请求都不发（网页版不需要顶部提示条，就不该为它出网）。
 */
import { useCallback, useEffect, useState } from 'react'
import { getApiBaseUrl } from '../api/client'

export interface UpdateStatus {
  /** 本站版本 */
  current: string
  /** source | docker | desktop */
  distribution: string
  /** stable | beta（beta 会把预发布一起算进来） */
  channel: string
  /** 部署方是否开启了更新检查 */
  enabled: boolean
  /** 是否拿到了一个可用的上游结论 */
  ok: boolean
  latest: string
  has_update: boolean
  release_url: string
  /** 与本机平台匹配的安装包直链（只有 desktop 发行版会给） */
  download_url: string
  published_at: string
  notes: string
  assets: { name: string; url: string; size: number }[]
  /** 最近一次成功检查的时间（ISO） */
  checked_at: string
  /** 本次响应是否来自后端缓存 */
  cached: boolean
  /** 上游暂时不可达：数字来自上一次成功检查 */
  stale: boolean
  /** 「立即检查」被节流 */
  throttled: boolean
  /** 空串 = 最近一次检查成功；否则是 timeout / unreachable / rate_limited … */
  error: string
}

let state: UpdateStatus | null = null
let inflight: Promise<UpdateStatus | null> | null = null
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((fn) => fn())
}

async function load(force: boolean): Promise<UpdateStatus | null> {
  if (!force && state) return state
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const url = getApiBaseUrl() + '/system/update-status' + (force ? '?force=1' : '')
      const res = await fetch(url)
      if (!res.ok) return null
      const data = (await res.json()) as UpdateStatus
      state = data
      emit()
      return data
    } catch {
      return null
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export function useUpdateStatus(enabled = true) {
  const [status, setStatus] = useState<UpdateStatus | null>(state)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const fn = () => setStatus(state)
    listeners.add(fn)
    return () => { listeners.delete(fn) }
  }, [])

  useEffect(() => {
    if (!enabled) return
    let alive = true
    if (!state) setLoading(true)
    load(false).then((s) => {
      if (!alive) return
      setLoading(false)
      setFailed(!s)
    })
    return () => { alive = false }
  }, [enabled])

  const refresh = useCallback(async () => {
    setLoading(true)
    const s = await load(true)
    setLoading(false)
    setFailed(!s)
    return s
  }, [])

  return { status, loading, failed, refresh }
}
