import { useCallback, useEffect, useState } from 'react'

/**
 * 侧栏「收起 / 展开」状态 —— 平台唯一实现。
 *
 * 语义跟管理页左栏一致：布尔态存 localStorage（'1' = 收起），刷新与切页都保持；
 * 手机端不参与（调用方自己把小屏下的侧栏做成抽屉，本来就收着）。
 *
 * 为什么抽出来：聊天列表、设置、DSH、控制台四处都要同一个动作，各写一份
 * 「useState + localStorage.getItem + setItem」必然有一处漏掉读或写，
 * 表现是「收起了，刷新又弹回来」——这种小 bug 事后极难有人复现。
 */
/** 侧栏收起后的宽度：四处同一档，收起时只留一枚展开按钮的宽度 */
export const RAIL_COLLAPSED_WIDTH = 52

export function useRailCollapsed(storageKey: string) {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(storageKey) === '1')

  useEffect(() => {
    localStorage.setItem(storageKey, collapsed ? '1' : '0')
  }, [storageKey, collapsed])

  const toggle = useCallback(() => setCollapsed((v) => !v), [])

  return [collapsed, toggle] as const
}
