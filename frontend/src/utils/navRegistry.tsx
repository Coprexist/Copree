/**
 * 导航项注册表 — 统一管理所有导航链接
 *
 * Sidebar.tsx 和 MobileNav.tsx 都从此文件获取导航数据，
 * 新增页面只需在此添加一项，无需修改多个文件。
 *
 * 用法：
 *   import { mainNavItems, type NavItem } from '../utils/navRegistry'
 */
import type { LucideIcon } from 'lucide-react'
import { MessageCircle, UsersRound, UserRound, Globe, Sparkles } from 'lucide-react'

/**
 * 导航项配色。
 *
 * 参考的是主流 IM 的做法（微信 / QQ / 抖音 / 小红书都一样）：未选中全是中性线条图标，
 * 只有当前项换成彩色图标 + 彩色文字。颜色只用来回答「我现在在哪」，
 * 不做背景色块——五项各配一块底色会让整条栏变成一排糖果按钮，视线没有落点。
 *
 * 类名必须写成完整字符串：Tailwind 只扫源码字面量，
 * 拼接出来的 `text-${tone}-400` 一个也不会生成。
 */
export type NavTone = 'primary' | 'mint' | 'accent' | 'rose' | 'slate'

/**
 * 每个栏目一组渐变色（描边用）。选中时图标本身的线条走这个渐变：
 * 形状还是线条图标，但上色方式从「单一灰」变成「从亮到深的过渡」，
 * 跟 QQ 选中项那种彩色图标同一个思路，只是不加填充色块。
 */
export const NAV_TONES: Record<NavTone, {
  /** 渐变起点（左上） */
  from: string
  /** 渐变终点（右下） */
  to: string
  /** 选中项的文字色 */
  label: string
}> = {
  primary: { from: '#c4b5fd', to: '#7c3aed', label: 'text-primary-400' },
  mint: { from: '#99f6e4', to: '#0d9488', label: 'text-mint-400' },
  accent: { from: '#fde68a', to: '#d97706', label: 'text-accent-400' },
  rose: { from: '#fecdd3', to: '#e11d48', label: 'text-rose-400' },
  slate: { from: '#e2e8f0', to: '#94a3b8', label: 'text-slate-300' },
}

export interface NavItem {
  /** 路由路径 */
  path: string
  /** i18n key（从 translations.ts 查找） */
  i18nKey: string
  /** lucide-react 图标组件 */
  icon: LucideIcon
  /** 选项卡配色（手机底部标签栏用） */
  tone?: NavTone
  /** 仅管理员可见 */
  adminOnly?: boolean
  /** 是否在主导航中隐藏（如 /setup 只在特定场景显示） */
  hidden?: boolean
  /** 是否匹配子路径（如 /chat/gm/:groupId） */
  matchSubPaths?: boolean
}

/**
 * 主导航项（显示在 Sidebar 和 MobileNav 底部标签栏）
 *
 * 添加新导航项只需在数组中追加一项。
 * 注意：世界商城（/market）不在主导航——入口在世界列表页内，
 * 避免侧边栏/手机底部占用主要位置。
 */
export const mainNavItems: NavItem[] = [
  // 图标统一挑圆润的几何：UsersRound / UserRound / Sparkles 的转角与端点都是圆的，
  // 比 Users / User / Bot 那种直角造型更贴小屏底栏的观感。
  { path: '/chat', i18nKey: 'nav:chat', icon: MessageCircle, matchSubPaths: true, tone: 'primary' },
  { path: '/worlds', i18nKey: 'nav:worlds', icon: Globe, tone: 'mint' },
  { path: '/list', i18nKey: 'nav:list', icon: UsersRound, tone: 'accent' },
  { path: '/agents', i18nKey: 'nav:ai', icon: Sparkles, tone: 'rose' },
  { path: '/me', i18nKey: 'nav:me', icon: UserRound, tone: 'slate' },
]

/**
 * 次要导航项（只在 Sidebar 展开时显示）
 */
export const secondaryNavItems: NavItem[] = [
  { path: '/settings', i18nKey: 'nav:settings', icon: UserRound },
]

/**
 * 管理员导航项
 */
export const adminNavItem: NavItem = {
  path: '/admin',
  i18nKey: 'nav:admin',
  icon: UserRound,  // Shield icon used inline in Sidebar
  adminOnly: true,
}

/**
 * 工具类：生成 nav-link 的 CSS class
 */
export const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 px-3 py-2.5 mx-2 rounded-card text-sm font-medium transition-all duration-200 ${
    isActive
      ? 'bg-primary-500/15 text-primary-600 dark:text-primary-300'
      : 'text-textSecondary hover:text-textPrimary hover:bg-elevated'
  }`

/**
 * 工具类：生成 collapsed 模式下的图标 CSS class
 */
export const navIconClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center justify-center w-10 h-10 rounded-card transition-all duration-200 ${
    isActive
      ? 'bg-primary-500/15 text-primary-600 dark:text-primary-300'
      : 'text-textSecondary hover:text-textPrimary hover:bg-elevated'
  }`
