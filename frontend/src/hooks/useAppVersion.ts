/**
 * 当前部署的版本号与发行方式（来源：\`/health\` 的 \`version\` / \`distribution\`）。
 *
 * 为什么不用构建期注入：前端与后端是两个产物，只有后端那一份是"当前部署的版本"
 * （发版时由 tag 与 \`app_version\` 强校验）。构建期塞进来的数字会随前端缓存过期而与后端分叉。
 *
 * 走裸 fetch 而不是 \`api.get\`：\`/health\` 在数据库降级时返回 503，但 body 里仍有 version，
 * 那种时候更该让人看见版本号。拉取失败就返回空串，界面不显示，绝不让它影响任何主流程。
 *
 * 发行方式（source / docker / desktop）决定「怎么升级」的引导文案，所以和版本号一起取——
 * 同一个接口、同一份缓存，界面不用为它多发一次请求。
 */
import { useEffect, useState } from 'react'
import { getApiBaseUrl } from '../api/client'

export interface RuntimeInfo {
  version: string
  distribution: string
}

const EMPTY: RuntimeInfo = { version: '', distribution: '' }

let cached: RuntimeInfo | null = null
let inflight: Promise<RuntimeInfo> | null = null

async function fetchRuntimeInfo(): Promise<RuntimeInfo> {
  if (cached) return cached
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const res = await fetch(getApiBaseUrl() + '/health')
      const data = await res.json()
      cached = {
        version: typeof data?.version === 'string' ? data.version : '',
        distribution: typeof data?.distribution === 'string' ? data.distribution : '',
      }
    } catch {
      cached = EMPTY            // 网络/解析失败：这一次不显示，下次组件挂载再试
    } finally {
      inflight = null
    }
    return cached ?? EMPTY
  })()
  return inflight
}

/** 版本号 + 发行方式（同一个模块级缓存，多个组件共用一次请求） */
export function useRuntimeInfo(): RuntimeInfo {
  const [info, setInfo] = useState<RuntimeInfo>(cached ?? EMPTY)
  useEffect(() => {
    if (cached) return
    let alive = true
    fetchRuntimeInfo().then((v) => { if (alive) setInfo(v) })
    return () => { alive = false }
  }, [])
  return info
}

export function useAppVersion(): string {
  return useRuntimeInfo().version
}

export function useDistribution(): string {
  return useRuntimeInfo().distribution
}
