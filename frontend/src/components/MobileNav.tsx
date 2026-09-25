import { useNavigate, useLocation } from 'react-router-dom'
import { useT } from '../i18n/I18nContext'
import { mainNavItems, type NavItem } from '../utils/navRegistry.tsx'
import { usePendingRequests } from '../hooks/usePendingRequests'

interface MobileNavProps {
  closeDrawer?: () => void
}

export default function MobileNav({ closeDrawer }: MobileNavProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const t = useT()
  const pendingRequests = usePendingRequests()

  const isActive = (item: NavItem) => {
    if (item.matchSubPaths) {
      return location.pathname.startsWith(item.path) || location.pathname === '/'
    }
    return location.pathname.startsWith(item.path)
  }

  const visibleItems = mainNavItems.filter(item => !item.hidden)
  const isCompact = visibleItems.length >= 6

  return (
    <nav
      className="md:hidden fixed bottom-0 left-0 right-0 z-modal bg-surface/95 backdrop-blur-lg border-t border-border"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
    >
      <div className="flex h-14">
        {visibleItems.map((item) => {
          const active = isActive(item)
          return (
            <button
              key={item.path}
              onClick={() => { closeDrawer?.(); navigate(item.path) }}
              className={`flex-1 flex flex-col items-center justify-center gap-0.5 transition-all duration-200 relative min-w-0 ${
                active ? 'text-primary-400' : 'text-textMuted'
              }`}
            >
              {active && (
                <div className="absolute top-0 h-0.5 bg-primary-400 rounded-full" style={{ left: '20%', right: '20%' }} />
              )}
              <div className="relative">
                <item.icon size={isCompact ? 20 : 22} strokeWidth={active ? 2.5 : 2} />
                {item.path === '/list' && pendingRequests > 0 && (
                  <span className="absolute -top-1 -right-1.5 w-2.5 h-2.5 rounded-full bg-rose-500 border-2 border-surface" />
                )}
                {active && (
                  <div className="absolute rounded-full ai-pulse-active opacity-50" style={{ inset: isCompact ? '-3px' : '-6px' }} />
                )}
              </div>
              <span className={`${isCompact ? 'text-[9px]' : 'text-3xs'} font-medium truncate max-w-full px-0.5`}>{t(item.i18nKey)}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
