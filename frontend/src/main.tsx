import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { router, createDemoRouter } from './App'
import { AuthProvider } from './context/AuthContext'
import { ThemeProvider } from './context/ThemeContext'
import { I18nProvider } from './i18n/I18nContext'
import ErrorBoundary from './components/ErrorBoundary'
import { ConfirmDialogHost } from './components/ui'
import EnterTransition from './components/EnterTransition'
import { setupDemo } from './demo/demoSetup'
import './index.css'
import 'katex/dist/katex.min.css'
// mhchem 化学式扩展（\ce / \pu）——side-effect 注册到 katex 实例，须在渲染前加载；
// 与 katex 一起走 vite 预构建（optimizeDeps.include）保证单实例
import 'katex/dist/contrib/mhchem.mjs'

const isDemo = window.location.pathname.includes('/Copree/') || window.location.hostname === 'coprexist.github.io'

if (isDemo) {
  setupDemo()
  // 演示模式：BrowserRouter 带 basename，匹配 /Copree/ 路径
  const base = window.location.pathname.startsWith('/Copree') ? '/Copree' : '/'
  const demoRouter = createDemoRouter(base)
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <ThemeProvider>
          <AuthProvider>
            <I18nProvider>
              <RouterProvider router={demoRouter} />
              <ConfirmDialogHost />
              <EnterTransition />
            </I18nProvider>
          </AuthProvider>
        </ThemeProvider>
      </ErrorBoundary>
    </React.StrictMode>,
  )
} else {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <ThemeProvider>
          <AuthProvider>
            <I18nProvider>
              <RouterProvider router={router} />
              <ConfirmDialogHost />
              <EnterTransition />
            </I18nProvider>
          </AuthProvider>
        </ThemeProvider>
      </ErrorBoundary>
    </React.StrictMode>,
  )
}
