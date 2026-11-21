import type { ReactNode } from 'react'

import { CHAT_HEADER_GAP } from './chatHeaderInset'

/**
 * 聊天面板顶部的提示条栈（重连、沉浸入口、以及以后加的任何一条）。
 *
 * 为什么收成一个组件：这些条以前各自 `absolute top: var(--header-inset)` 写在自己那一段 JSX 里，
 * 两条同时出现就叠在一起、且都拿"头部让位量"当自己的位置（比头部下沿低 0.5rem），
 * 看起来就是"条和顶部之间有空隙"。现在统一：位置只由这里算、顺序只由 order 决定、
 * 高度只由调用方按条数 × NOTICE_H 让给消息区（--header-strip）。
 *
 * 它**浮在消息上**（不占布局）——消息从它下面滚过去；占位由调用方给滚动容器加 padding-top。
 */

export type NoticeTone = 'accent' | 'primary' | 'danger'

export interface NoticeItem {
  id: string
  /** 越大越靠下：同一时刻多条时按它排，别用插入顺序碰运气 */
  order: number
  tone: NoticeTone
  icon?: ReactNode
  text: string
  /** 整条可点（如「在沉浸式界面打开」）；不传就是纯提示 */
  onClick?: () => void
  title?: string
}

/** 一条的高度：text-xs 行高 + 上下 py-1.5。消息区顶部留白 = 条数 × 它 */
export const NOTICE_H = '1.8125rem'

const TONE: Record<NoticeTone, string> = {
  accent: 'bg-accent-500/15 border-accent-500/20 text-accent-400',
  primary: 'bg-primary-500/10 border-primary-500/20 text-primary-400',
  danger: 'bg-rose-500/15 border-rose-500/20 text-rose-400',
}

const HOVER: Record<NoticeTone, string> = {
  accent: 'hover:bg-accent-500/20',
  primary: 'hover:bg-primary-500/15',
  danger: 'hover:bg-rose-500/20',
}

export default function NoticeStack({ items }: { items: NoticeItem[] }) {
  if (items.length === 0) return null
  const sorted = [...items].sort((a, b) => a.order - b.order)
  return (
    // 贴头部条的下沿：CHAT_HEADER_INSET 是"消息起点"（多一段呼吸间距），这里要的是"头部下沿"
    <div className="absolute inset-x-0 z-modal" style={{ top: `calc(var(--header-inset, 0px) - ${CHAT_HEADER_GAP})` }}>
      {sorted.map((item) => {
        const cls = `w-full flex items-center justify-center gap-1.5 px-4 py-1.5 text-xs font-medium border-b transition-colors ${TONE[item.tone]}`
        const body = <>{item.icon}{item.text}</>
        return item.onClick ? (
          <button key={item.id} type="button" title={item.title} onClick={item.onClick} className={`${cls} ${HOVER[item.tone]}`}>
            {body}
          </button>
        ) : (
          <div key={item.id} title={item.title} className={cls}>{body}</div>
        )
      })}
    </div>
  )
}
