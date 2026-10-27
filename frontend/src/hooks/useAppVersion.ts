/**
 * 当前部署的版本号（来源：`/health` 的 `version`，也就是 backend 的 `settings.app_version`）。
 *
 * 为什么不用构建期注入：前端与后端是两个产物，只有后端那一份是"当前部署的版本"
 * （发版时由 tag 与 `app_version` 强校验）。构建期塞进来的数字会随前端缓存过期而与后端分叉。
 *
 * 走裸 fetch 而不是 `api.get`：`/health` 在数据库降级时返回 503，但 body 里仍有 version，
 * 那种时候更该让人看见版本号。拉取失败就返回空串，界面不显示，绝不让它影响任何主流程。
 */
import { useEffect, useState } from 'react'
import { getApiBaseUrl } from '../api/client'

let cached: string | null = null
let inflight: Promise<string> | null = null

async function fetchVersion(): Promise<string> {
  if (cached !== null) return cached
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const res = await fetch(`${getApiBaseUrl()}/health`)
      const data = await res.json()
      cached = typeof data?.version === 'string' ? data.version : ''
    } catch {
      cached = ''          // 网络/解析失败：这一次不显示，下次组件挂载再试
    } finally {
      inflight = null
    }
    return cached ?? ''
  })()
  return inflight
}

export function useAppVersion(): string {
  const [version, setVersion] = useState(cached ?? '')
  useEffect(() => {
    if (cached !== null) return
    let alive = true
    fetchVersion().then((v) => { if (alive) setVersion(v) })
    return () => { alive = false }
  }, [])
  return version
}
