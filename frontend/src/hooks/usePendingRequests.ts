import { useState, useEffect } from 'react'
import { api } from '../api/client'

/** 轮询兜底周期：别人发来的申请靠它，自己处理完靠广播即时刷新 */
export const REQUESTS_POLL_MS = 30_000

/**
 * 待处理申请变化广播（2026-09-21 用户反馈：接受申请后红点要等半天才消失）。
 * 只靠轮询，处理完最多滞后一个周期；就地广播一次，所有入口共用同一个 hook 一起刷新。
 * 名字不再带"好友"——申请列表里还有入群申请与成员邀请审批。
 */
export const REQUESTS_CHANGED = 'requests-changed'

/** 任何会改变待处理申请数量的写操作完成后调用（接受/拒绝/撤回/发送/审批）。 */
export function notifyRequestsChanged(): void {
  window.dispatchEvent(new Event(REQUESTS_CHANGED))
}

/** 申请列表条目。kind 决定分区与审批动作，字段口径由后端 /requests/pending 统一给出。 */
export interface PendingRequest {
  kind: 'friend' | 'group_join' | 'group_invite'
  id: number
  /** 发起人：好友申请是申请人，入群申请是申请人，邀请审批是邀请人 */
  user_id: number
  user_name: string | null
  avatar_url: string | null
  message: string | null
  created_at: string | null
  group_id: number | null
  group_name: string | null
  /** 仅邀请审批：被邀请的人 */
  target_id: number | null
  target_name: string | null
}

/**
 * 拉取待处理申请（唯一入口：红点只数条数，申请列表页要整条，都从这里走）。
 * 接口未就绪时抛错，由调用方决定是静默还是提示——但绝不能把异常吞成"0 条"。
 */
export async function fetchPendingRequests(): Promise<PendingRequest[]> {
  const data = await api.get<PendingRequest[]>('/requests/pending')
  return Array.isArray(data) ? data : []
}

/**
 * 待处理申请总数（侧边栏与底部导航的红点口径）。
 * 好友申请 + 入群申请 + 待我审批的邀请都算一条，由后端统一聚合，前端不再各算各的。
 */
export function usePendingRequests(): number {
  const [count, setCount] = useState(0)

  useEffect(() => {
    let mounted = true

    const fetchCount = () => {
      fetchPendingRequests()
        .then((items) => { if (mounted) setCount(items.length) })
        .catch(() => {})
    }

    fetchCount()
    const iv = setInterval(fetchCount, REQUESTS_POLL_MS)
    // 轮询是兜底（别人发来时用），本地处理完靠广播即时刷新
    window.addEventListener(REQUESTS_CHANGED, fetchCount)
    return () => {
      mounted = false
      clearInterval(iv)
      window.removeEventListener(REQUESTS_CHANGED, fetchCount)
    }
  }, [])

  return count
}
