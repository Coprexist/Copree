import { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { Shield, LogOut, Menu, X, ChevronLeft, BookOpen, Settings, Library } from 'lucide-react'
import { MANUAL_URL } from '../constants'
import SearchOverlay from './SearchOverlay'
import { useT } from '../i18n/I18nContext'
import { mainNavItems, navLinkClass, navIconClass } from '../utils/navRegistry.tsx'
import { usePendingRequests } from '../hooks/usePendingRequests'

export default function Sidebar({ mobile, onClose, translucent }: { mobile?: boolean; onClose?: () => void; translucent?: boolean }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const t = useT()
  const pendingRequests = usePendingRequests()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <aside
      className={`${
        collapsed ? 'w-16 h-full' : mobile ? 'w-full h-full' : 'w-60 h-full'
      } ${translucent ? 'bg-surface/70' : 'bg-surface'} border-r border-border flex flex-col transition-all duration-200 shrink-0`}
    >
      {/* 头部 */}
      <div className="h-14 px-4 border-b border-border flex items-center justify-between shrink-0">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-control flex items-center justify-center shadow shadow-primary-500/30 overflow-hidden">
              <img src="/logo.png" alt="Copree" className="w-full h-full object-contain" />
            </div>
            <span className="text-base font-bold text-textPrimary tracking-tight">Copree</span>
          </div>
        )}
        {mobile ? (
          <button
            onClick={onClose}
            className="icon-btn-sm text-textMuted ml-auto"
          >
            <X size={20} />
          </button>
        ) : (
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="p-1.5 rounded-control hover:bg-elevated text-textMuted hover:text-textSecondary transition-colors"
            title={collapsed ? t('sidebar.expand') : t('sidebar.collapse')}
          >
            {collapsed ? <Menu size={16} /> : <ChevronLeft size={16} />}
          </button>
        )}
      </div>

      {/* 搜索框 */}
      {!collapsed && (
        <div className="px-3 py-2 border-b border-border">
          <SearchOverlay />
        </div>
      )}

      {/* 用户信息 */}
      {!collapsed && user && (
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-sm font-medium text-textPrimary truncate">{user.username}</p>
            <p className="text-xs text-textMuted">
              {user.role === 'admin' ? (
                <span className="text-accent-400">{t('sidebar.adminPanel')}</span>
              ) : (
                <span>{t('sidebar.quota') + ' ' + (user.ai_quota ?? 0) + ' · ' + t('sidebar.balance') + ' ' + ((user as any).total_effective ?? (user as any).api_credit ?? 0)}</span>
              )}
            </p>
          </div>
        </div>
      )}

      {/* 主导航 — 展开模式 */}
      {!collapsed && (
        <nav className="flex-1 py-3 space-y-0.5">
          {mainNavItems.filter(item => !item.hidden).map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={!item.matchSubPaths}
              onClick={() => { onClose?.() }}
              className={navLinkClass}
            >
              <item.icon size={18} />
              <span>{t(item.i18nKey)}</span>
              {item.path === '/list' && pendingRequests > 0 && (
                <span className="ml-auto w-2 h-2 rounded-full bg-rose-500" />
              )}
            </NavLink>
          ))}

          <NavLink to="/settings" onClick={() => { onClose?.() }} className={navLinkClass}>
            <Settings size={18} />
            <span>{t('nav.settings')}</span>
          </NavLink>

          {user?.role === 'admin' && (
            <NavLink to="/admin" onClick={() => { onClose?.() }} className={navLinkClass}>
              <Shield size={18} />
              <span>{t('nav.admin')}</span>
            </NavLink>
          )}

          <NavLink
            to={MANUAL_URL}
            onClick={() => { onClose?.() }}
            className={navLinkClass}
          >
            <BookOpen size={18} />
            <span>{t('nav.manual')}</span>
          </NavLink>
        </nav>
      )}

      {/* 主导航 — 折叠模式 */}
      {collapsed && (
        <nav className="flex-1 py-3 space-y-0.5 flex flex-col items-center">
          {mainNavItems.filter(item => !item.hidden).map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={!item.matchSubPaths}
              onClick={() => { onClose?.() }}
              className={navIconClass}
              title={t(item.i18nKey)}
            >
              <span className="relative">
                <item.icon size={18} />
                {item.path === '/list' && pendingRequests > 0 && (
                  <span className="absolute -top-1 -right-1.5 w-2 h-2 rounded-full bg-rose-500" />
                )}
              </span>
            </NavLink>
          ))}
          {user?.role === 'admin' && (
            <NavLink
              to="/admin"
              onClick={() => { onClose?.() }}
              className={navIconClass}
              title={t('sidebar.adminPanel')}
            >
              <Shield size={18} />
            </NavLink>
          )}
          <NavLink
            to={MANUAL_URL}
            onClick={() => { onClose?.() }}
            className={navIconClass}
            title={t('sidebar.usageManual')}
          >
            <BookOpen size={18} />
          </NavLink>
        </nav>
      )}

      {/* 小工具（三根杠菜单） */}
      <div className="p-2 border-t border-border shrink-0 relative">
        <button
          onClick={() => setToolsOpen(!toolsOpen)}
          className={`flex items-center rounded-card text-textSecondary hover:text-textPrimary hover:bg-elevated transition-all duration-200 text-sm ${
            collapsed ? 'justify-center w-10 h-10' : 'gap-3 w-full px-3 py-2.5'
          }`}
          title={t('sidebar.tools')}
        >
          <Menu size={18} />
          {!collapsed && <span>{t('sidebar.tools')}</span>}
        </button>
        {toolsOpen && (
          <>
            <div className="fixed inset-0 z-drawer" onClick={() => setToolsOpen(false)} />
            <div
              className={`absolute bottom-full mb-2 z-modal w-44 rounded-card border border-border bg-surface shadow-2xl py-1.5 ${collapsed ? 'left-0' : 'left-2'}`}
            >
              <button
                onClick={() => { setToolsOpen(false); navigate('/study') }}
                className="w-full flex items-center gap-3 px-3 py-2 text-sm text-textSecondary hover:text-textPrimary hover:bg-elevated transition-colors"
              >
                <Library size={16} strokeWidth={2} />
                <span>{t('sidebar.studyRoom')}</span>
              </button>
            </div>
          </>
        )}
      </div>

      {/* 退出 */}
      <div className="p-2 border-t border-border shrink-0">
        <button
          onClick={handleLogout}
          className={`flex items-center rounded-card text-textSecondary hover:text-rose-400 hover:bg-rose-500/10 transition-all duration-200 text-sm ${
            collapsed ? 'justify-center w-10 h-10' : 'gap-3 w-full px-3 py-2.5'
          }`}
          title={collapsed ? t('sidebar.logout') : undefined}
        >
          <LogOut size={18} />
          {!collapsed && <span>{t('sidebar.logout')}</span>}
        </button>
      </div>
    </aside>
  )
}
