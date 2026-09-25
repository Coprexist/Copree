import { ButtonHTMLAttributes, HTMLAttributes } from 'react'

/**
 * 浮层配方 —— 全站的手写浮层都从这里取。对外只有四个出口：
 * `MenuPanel`（面板）、`MenuItem`（行）两个组件，加上调用方要直接贴在 div 上的
 * `MENU_CAPTION`（整句表头）与 `INSET_CARD`（面内卡片）；底色/行态的行内配方
 * 由这两个组件包住，不再单独导出（没人用就是死接口）。
 * 短标签表头（大写 + 字距）等真有浮层需要时再加，不为"以后可能用"留导出。
 *
 * 为什么面板用 surface 而不是 elevated：浅色主题里 elevated 比 surface 更灰，
 * 整块 elevated 在灰底页面上就是"一块灰"，行与选中态也没法再拉开层次；
 * 改成 surface 面板 + elevated（hover）/ primary 淡底（选中），深浅两套主题各有一档对比，
 * 抬起来的感觉交给 1px 细边框 + shadow-xl（菜单档阴影，不再往上堆）。
 * max-w 是窄屏兜底：右对齐的浮层不至于超出视口被裁一半。
 */
const MENU_PANEL =
  'rounded-card border border-border bg-surface shadow-xl overflow-hidden max-w-[calc(100vw-2rem)]'

/** 长句表头（排队提示这类整句说明）：不加大写与字距，长中文句子加了只会发虚 */
export const MENU_CAPTION = 'px-3 py-1.5 text-3xs text-textMuted border-b border-border'

/** 行：默认次级色，hover 抬到 elevated 面 + 主文本色 */
const MENU_ROW =
  'w-full text-left px-3 py-1.5 text-2xs text-textSecondary hover:bg-elevated hover:text-textPrimary transition-colors disabled:opacity-50'

/** 当前项 / 键盘高亮项：站内主导的 primary 淡底口径（bg-primary-500/15 + text-primary-400） */
const MENU_ROW_ACTIVE = 'bg-primary-500/15 text-primary-400'

/**
 * 面内可点卡片（建议卡 / 继续卡这类贴在消息里的行）：
 * 用 canvas 凹一档、hover 抬到 elevated；它属于内容层，不跟浮层抢同一套底色。
 */
export const INSET_CARD = 'rounded-control border border-border bg-canvas'

/** 浮层面板（div）：定位交给调用方（anchor 各不相同），底色/边框/阴影/圆角统一在这里 */
export function MenuPanel({ className = '', children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`${MENU_PANEL} ${className}`} {...rest}>
      {children}
    </div>
  )
}

/** 浮层行（button）：默认 type=button——浮层可能挂在表单里，裸 button 会误触提交 */
export function MenuItem({ active = false, className = '', children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      className={`${MENU_ROW} ${active ? MENU_ROW_ACTIVE : ''} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}
