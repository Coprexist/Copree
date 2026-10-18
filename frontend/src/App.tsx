import { createBrowserRouter, Navigate, Outlet, useLocation } from 'react-router-dom'
import { lazy } from 'react'
import { useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import { getPublicRoutes, getProtectedRoutes } from './utils/pageRegistry'
import { initEmbedBridge, setEmbedNavigator, isEmbedded } from './embed/bridge'

const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

function ProtectedLayout() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />

  // 新用户需先完成初始化设置向导。创建 AI 那一步直接跳平台的整页创建流程，
  // 它在向导完成之前也得能进——否则点「创建 AI」会被这里弹回向导首页
  const SETUP_PATHS = ['/setup', '/agents/create']
  if (!user.setup_completed && !SETUP_PATHS.includes(location.pathname)) {
    return <Navigate to="/setup" replace />
  }

  // 桌面端首次启动：未配置实例地址则跳转到配置页
  if (
    '__TAURI_INTERNALS__' in window &&
    !localStorage.getItem('instance_url') &&
    location.pathname !== '/instance-setup'
  ) {
    return <Navigate to="/instance-setup" replace />
  }

  return <Layout />
}

function AdminGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user || user.role !== 'admin') return <Navigate to="/chat" replace />
  return <>{children}</>
}

const _routes = [
  ...getPublicRoutes(),
  {
    path: '/',
    element: <ProtectedLayout />,
    children: getProtectedRoutes(AdminGuard),
  },
  { path: '*', element: <NotFoundPage /> },
]

export const routes = _routes

// 嵌入模式：以宿主挂载前缀为 basename（DSH 托管于 /copree-ui），否则路由
// 期望 /worlds 而实际 URL 是 /copree-ui/worlds → 全部 404
const ROUTER_BASENAME = isEmbedded() ? '/copree-ui' : undefined
export const router = createBrowserRouter(_routes, ROUTER_BASENAME !== undefined ? { basename: ROUTER_BASENAME } : undefined)

// 嵌入模式：注册导航器并初始化嵌入桥（接收宿主导航指令、上报联系人列表）
if (isEmbedded()) {
  setEmbedNavigator((path) => router.navigate(path))
  initEmbedBridge()
}

/** 给 demo 模式用：创建带 basename 的 BrowserRouter */
export function createDemoRouter(basename: string) {
  return createBrowserRouter(_routes, { basename })
}
