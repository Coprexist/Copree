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
import { MessageCircle, Users, Bot, User, Globe } from 'lucide-react'

export interface NavItem {
  /** 路由路径 */
  path: string
  /** i18n key（从 translations.ts 查找） */
  i18nKey: string
  /** lucide-react 图标组件 */
  icon: LucideIcon
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
  { path: '/chat', i18nKey: 'nav.chat', icon: MessageCircle, matchSubPaths: true },
  { path: '/worlds', i18nKey: 'nav.worlds', icon: Globe },
  { path: '/list', i18nKey: 'nav.list', icon: Users },
  { path: '/agents', i18nKey: 'nav.ai', icon: Bot },
  { path: '/me', i18nKey: 'nav.me', icon: User },
]

/**
 * 次要导航项（只在 Sidebar 展开时显示）
 */
export const secondaryNavItems: NavItem[] = [
  { path: '/settings', i18nKey: 'nav.settings', icon: User },
]

/**
 * 管理员导航项
 */
export const adminNavItem: NavItem = {
  path: '/admin',
  i18nKey: 'nav.admin',
  icon: User,  // Shield icon used inline in Sidebar
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
