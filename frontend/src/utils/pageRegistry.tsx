/**
 * 页面路由注册表 — 统一管理所有前端路由
 *
 * 新增页面只需在此文件添加一行，无需修改 App.tsx。
 *
 * 用法：
 *   1. 在 pages/ 下创建组件
 *   2. 在此文件 import 并添加到 registerRoutes() 数组
 *   3. App.tsx 自动扫描注册
 *
 * 代码分割：非首屏页面用 React.lazy 延迟加载，减小初始 JS 体积
 */
import type { RouteObject } from 'react-router-dom'
import { Navigate } from 'react-router-dom'
import { lazy, type ComponentType, type ReactNode } from 'react'
import { isEmbedded } from '../embed/bridge'
import LoginPage from '../pages/LoginPage'
// ChatPage 首屏常用，保持急加载
import ChatPage from '../pages/ChatPage'

// ── 延迟加载的非首屏页面 ──
const DMPage = lazy(() => import('../pages/DMPage'))
const AgentsPage = lazy(() => import('../pages/AgentsPage'))
const AgentCreatePage = lazy(() => import('../pages/AgentCreatePage'))
const AgentDetailPage = lazy(() => import('../pages/AgentDetailPage'))
const SettingsPage = lazy(() => import('../pages/SettingsPage'))
const MePage = lazy(() => import('../pages/MePage'))
const StoragePage = lazy(() => import('../pages/StoragePage'))
const UsagePage = lazy(() => import('../pages/UsagePage'))
const ConsolePage = lazy(() => import('../pages/console/ConsolePage'))
const ListPage = lazy(() => import('../pages/ListPage'))
const SetupPage = lazy(() => import('../pages/SetupPage'))
const ManualPage = lazy(() => import('../pages/ManualPage'))
const InstanceSetupPage = lazy(() => import('../pages/InstanceSetupPage'))
const LocalModelPage = lazy(() => import('../pages/LocalModelPage'))
const WorldsPage = lazy(() => import('../pages/WorldsPage'))
const MarketPage = lazy(() => import('../pages/MarketPage'))
const MarketPublishPage = lazy(() => import('../pages/MarketPublishPage'))
const WorldDesignPage = lazy(() => import('../pages/WorldDesignPage'))
const WorldViewPage = lazy(() => import('../pages/WorldViewPage'))
const StudyRoomPage = lazy(() => import('../pages/StudyRoomPage'))
const NotFoundPage = lazy(() => import('../pages/NotFoundPage'))
const DshChatPage = lazy(() => import('../pages/DshChatPage'))

// ── 路由定义类型 ──

export interface RouteDef {
  /** 路由路径（相对于父路由，不加前导 /） */
  path?: string
  /** 页面组件（自动注入，无需 JSX） */
  Component?: ComponentType<any>
  /** 如果是重定向，指定目标路径 */
  redirect?: string
  /** 仅管理员可见 */
  adminOnly?: boolean
  /** 是否为 index 路由 */
  index?: boolean
  /** 嵌套子路由 */
  children?: RouteDef[]
}

// ── 内置路由守卫 ──

export function AdminGuard({ children }: { children: ReactNode }) {
  // 实际实现在 App.tsx 中，这里只是占位
  return <>{children}</>
}

// ── 注册所有路由 ──

/**
 * 公开路由（无需登录）
 */
export function getPublicRoutes(): RouteObject[] {
  return [
    { path: '/login', element: <LoginPage /> },
  ]
}

/**
 * 是否停在某个具体会话的聊天详情页（群聊 / 私信）。
 *
 * 底部导航栏与 main 的滚动策略都看这个判定。路由表就在本文件，
 * 判定也放这里：两处必须同步，分开放必然漂移——群聊路由从 /chat/:id
 * 改成 /chat/gm/:id 时，Layout 里那条 /^\/chat\/(dm\/[^/]+|\d+)/ 就再也没命中过，
 * 结果是进入群聊后底部导航栏一直压着输入框。
 */
export function isChatDetailPath(pathname: string): boolean {
  return /^\/chat\/(?:gm|dm)\/[^/]+/.test(pathname)
}

/**
 * 是否渲染悬浮底部导航（false 的页面不渲染，也不留它的余量）。
 *
 * 判据只有两条：底部要输入文字的地方（胶囊会挡住输入框），以及整屏沉浸的画面。
 * 其余页面——设置、列表、群视界、我的——留不留都行，一律保留：底部导航是站内
 * 唯一的全局入口，能留就留着。
 * 判定和路由一样放本文件，新增这类页面时改这里一处，Layout 只负责调用。
 */
export function showsBottomNav(pathname: string): boolean {
  if (isEmbedded()) return false                     // ?embed=1：宿主页面自己管导航
  if (isChatDetailPath(pathname)) return false       // 群聊/私信：底部是输入框
  if (/^\/dm\/[^/]+/.test(pathname)) return false       // 旧地址，DMPage 会转成 /chat/dm/...
  if (/^\/dsh(\/|$)/.test(pathname)) return false        // DSH 插件对话页：底部同样是输入框
  if (/^\/world-view(\/|$)/.test(pathname)) return false // 世界沉浸：整页是画面，主要目的是沉浸
  if (/^\/agents\/create(\/|$)/.test(pathname)) return false // 创建向导：底部是输入框 + 「直接创建」主按钮
  return true
}

/**
 * 内容直接铺在 Layout 的 main 里滚动的页面。
 *
 * 默认反过来定：页面根是 h-full、内部自带 overflow-y-auto，滚动与底部余量都归页面
 * 自己那个容器（挂 .pb-nav）。只有把内容整个交给 main 的页面才写进这个名单。
 *
 * 为什么不能反过来（main 统一加内边距、页面自己再挂 .pb-nav）：那样 main 的内容盒
 * 被压矮一截，页面内部那个滚动容器的下沿正好停在悬浮胶囊上沿，内容永远滚不到胶囊
 * 后面去——胶囊只剩一条空底色，玻璃效果全无，而且白白少掉一屏可滚高度。
 * 名单和路由放一起，新增「铺在 main 里滚动」的页面时只改这里。
 */
const MAIN_SCROLL_PATHS = ['/manual']

/** 页面内容是否直接由 Layout 的 main 滚动（决定 main 要不要留底部导航的余量） */
export function scrollsInMain(pathname: string): boolean {
  return MAIN_SCROLL_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'))
}

/**
 * 受保护路由（需要登录，放在 ProtectedLayout 下）
 * 返回 RouteObject[] 供 createBrowserRouter 使用
 */
export function getProtectedRoutes(AdminGuardComponent: ComponentType<{ children: ReactNode }> = AdminGuard): RouteObject[] {
  const A = AdminGuardComponent
  return [
    { index: true, element: <Navigate to="/chat" replace /> },
    { path: 'chat', element: <ChatPage /> },
    { path: 'chat/gm/:groupId', element: <ChatPage /> },
    { path: 'dm', element: <Navigate to="/chat" replace /> },
    { path: 'dm/:sessionId', element: <DMPage /> },
    { path: 'chat/dm/:sessionId', element: <ChatPage /> },
    { path: 'list', element: <ListPage /> },
    // 旧地址兜底：改名前的 /friends 还留在书签、历史记录与外部链接里
    { path: 'friends', element: <Navigate to="/list" replace /> },
    { path: 'agents', element: <AgentsPage /> },
    { path: 'agents/create', element: <AgentCreatePage /> },
    { path: 'agents/:id', element: <AgentDetailPage /> },
    { path: 'me', element: <MePage /> },
    { path: 'me/usage', element: <UsagePage /> },
    { path: 'me/storage', element: <StoragePage /> },
    { path: 'settings', element: <SettingsPage /> },
    { path: 'setup', element: <SetupPage /> },
    { path: 'instance-setup', element: <InstanceSetupPage /> },
    { path: 'local-models', element: <LocalModelPage /> },
    // 群视界
    { path: 'worlds', element: <WorldsPage /> },
    { path: 'market', element: <MarketPage /> },
    { path: 'market/publish', element: <MarketPublishPage /> },
    { path: 'worlds/:worldId/design', element: <WorldDesignPage /> },
    { path: 'world-view/:worldId', element: <WorldViewPage /> },
    { path: 'study', element: <StudyRoomPage /> },
    { path: 'manual', element: <ManualPage /> },
    { path: 'manual/admin', element: <ManualPage /> },
    { path: 'admin', element: <A><ConsolePage /></A> },
    // DSH 对话：放大后的群视界对话版式，桥接 DSH 本体会话（管理员）
    { path: 'dsh', element: <A><DshChatPage /></A> },
  ]
}
