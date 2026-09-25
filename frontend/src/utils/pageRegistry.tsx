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
import LoginPage from '../pages/LoginPage'
// ChatPage 首屏常用，保持急加载
import ChatPage from '../pages/ChatPage'

// ── 延迟加载的非首屏页面 ──
const DMPage = lazy(() => import('../pages/DMPage'))
const AgentsPage = lazy(() => import('../pages/AgentsPage'))
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
