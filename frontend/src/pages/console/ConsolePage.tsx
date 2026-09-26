import { useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, BookOpen, ChevronRight, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { MANUAL_URL, ADMIN_MANUAL_URL } from '../../constants'
import { useT } from '../../i18n/I18nContext'
import { useResizableSidebar } from '../../hooks/useResizableSidebar'
import { CONSOLE_WORKSPACES, findConsoleItem, workspaceOf } from './workspaces'

/**
 * 控制台外壳 — 两级导航（上面工作区，左边该工作区的页面）。
 *
 * 为什么两级：管理工作项只会越来越多，一条 20 项的导航栏谁都扫不完。工作区是"大方向"，
 * 每个工作区的左边栏只有 2~6 项，一眼能看完；新增功能时只往对应工作区里加一行。
 *
 * 手机端不参与：仍然是"分组列表 → 详情"两步，交互和以前完全一样。
 *
 * 进 /admin 时应用侧边栏会收起（Layout 按路径判断），所以导航栏底部必须留一个「返回应用」出口。
 */
const RAIL_KEY = 'admin_rail_collapsed'

const railItemClass = (collapsed: boolean, active: boolean) =>
  'w-full flex items-center gap-2 py-1.5 text-sm transition-colors text-left ' +
  (collapsed ? 'justify-center px-0 ' : 'px-3 ') +
  (active
    ? 'bg-primary-500/10 text-primary-600 dark:text-primary-300 border-r-2 border-primary-400'
    : 'text-textSecondary hover:bg-elevated hover:text-textPrimary border-r-2 border-transparent')

export default function ConsolePage() {
  const t = useT()
  const [searchParams, setSearchParams] = useSearchParams()
  const current = findConsoleItem(searchParams.get('tab'))
  const [activeKey, setActiveKey] = useState(current.key)
  const workspace = workspaceOf(activeKey)
  const [mobileView, setMobileView] = useState<'list' | 'detail'>(searchParams.get('tab') ? 'detail' : 'list')
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(RAIL_KEY) === '1')
  // 每个工作区记住上次看的那一页：切回来还是原来那页，不用重新找
  const lastVisited = useRef<Record<string, string>>({ [workspace.key]: current.key })
  const navigate = useNavigate()
  const sidebarRef = useRef<HTMLDivElement>(null)
  const { sidebarWidth, handleResizeStart } = useResizableSidebar('admin_sidebar_width', sidebarRef)

  const ActiveTab = findConsoleItem(activeKey).Component

  const openItem = (key: string) => {
    setActiveKey(key)
    lastVisited.current[workspaceOf(key).key] = key
    setSearchParams({ tab: key })
  }

  const openWorkspace = (key: string) => {
    const target = CONSOLE_WORKSPACES.find(w => w.key === key)
    if (!target) return
    openItem(lastVisited.current[key] || target.items[0].key)
  }

  const toggleRail = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(RAIL_KEY, next ? '1' : '0')
  }

  return (
    <div className="h-full flex flex-col bg-canvas">
      {/* 头部：控制台标题 + 工作区页签（桌面端）+ 手册入口 */}
      <div className="px-4 md:px-6 h-12 border-b border-border bg-surface shrink-0 flex items-center gap-2">
        {mobileView === 'detail' ? (
          <button onClick={() => setMobileView('list')} className="icon-btn-sm md:hidden -ml-1 text-textSecondary" title={t('admin.backToList')}>
            <ArrowLeft size={20} />
          </button>
        ) : (
          <button onClick={() => navigate('/me')} className="icon-btn-sm md:hidden -ml-1 text-textSecondary" title={t('admin.backToMe')}>
            <ArrowLeft size={20} />
          </button>
        )}
        <h1 className="text-sm font-semibold text-textPrimary shrink-0">{t('admin.title')}</h1>

        <nav className="hidden md:flex items-center gap-0.5 ml-3 min-w-0">
          {CONSOLE_WORKSPACES.map(w => (
            <button
              key={w.key}
              onClick={() => openWorkspace(w.key)}
              className={'flex items-center gap-1.5 px-3 h-8 rounded-control text-sm transition-colors shrink-0 ' +
                (w.key === workspace.key
                  ? 'bg-primary-500/10 text-primary-600 dark:text-primary-300'
                  : 'text-textSecondary hover:bg-elevated hover:text-textPrimary')}
            >
              <w.icon size={15} />
              <span className="truncate">{t(w.labelKey)}</span>
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2 shrink-0">
          <Link to={MANUAL_URL} className="hidden sm:inline-flex items-center gap-1 text-xs text-textMuted hover:text-textPrimary transition-colors">
            <BookOpen size={13} /> {t('nav.manual')}
          </Link>
          <Link to={ADMIN_MANUAL_URL} className="hidden sm:inline-flex items-center gap-1 text-xs text-textMuted hover:text-textPrimary transition-colors">
            <BookOpen size={13} /> {t('nav.adminManual')}
          </Link>
        </div>
      </div>

      {/* 桌面端：左边当前工作区的页面列表 + 右边内容 */}
      <div className="hidden md:flex flex-1 overflow-hidden">
        <div
          ref={sidebarRef}
          className="shrink-0 relative border-r border-border bg-surface flex flex-col min-h-0 overflow-hidden"
          style={{ width: collapsed ? 52 : sidebarWidth }}
        >
          {/* 滚动只发生在内层：外层 overflow-hidden 才不会让拖拽手柄的溢出变成横向滚动条
              （overflow-y-auto 会把 overflow-x 一起算成 auto，手柄原来挂在 -right-1.5 上） */}
          <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden flex flex-col">
            <button
              onClick={toggleRail}
              className="icon-btn-sm text-textMuted hover:text-textPrimary self-end mr-2 mt-2 shrink-0"
              title={collapsed ? t('admin.expandRail') : t('admin.collapseRail')}
            >
              {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>

            {!collapsed && (
              <div className="px-3 h-7 font-medium text-2xs text-textMuted uppercase tracking-wider flex items-center shrink-0">
                {t(workspace.labelKey)}
              </div>
            )}

            <div className="py-0.5">
              {workspace.items.map(item => (
                <button
                  key={item.key}
                  onClick={() => openItem(item.key)}
                  title={collapsed ? t(item.labelKey) : undefined}
                  className={railItemClass(collapsed, activeKey === item.key)}
                >
                  <item.icon size={16} className="shrink-0" />
                  {!collapsed && <span className="truncate">{t(item.labelKey)}</span>}
                </button>
              ))}
            </div>

            {/* 出口：应用侧边栏在这里是收起的，回应用只能从控制台走 */}
            <div className="mt-auto border-t border-border shrink-0">
              <button
                onClick={() => navigate('/chat')}
                title={t('admin.backToApp')}
                className="w-full flex items-center gap-2 py-2 text-sm text-textSecondary hover:bg-elevated hover:text-textPrimary transition-colors justify-center px-0"
              >
                <ArrowLeft size={16} className="shrink-0" />
                {!collapsed && <span className="truncate">{t('admin.backToApp')}</span>}
              </button>
            </div>
          </div>

          {/* 拖拽手柄贴着侧栏右缘（在内部，不会被 overflow-hidden 裁掉，也不制造溢出） */}
          {!collapsed && (
            <div
              className="absolute top-0 right-0 w-1.5 h-full cursor-col-resize hover:bg-primary-400/30 active:bg-primary-400/50 transition-colors z-toast"
              onMouseDown={handleResizeStart}
            />
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-4 xl:p-6 min-w-0">
          <ActiveTab />
        </div>
      </div>

      {/* 移动端：分组列表（全部工作区平铺，和以前一模一样） */}
      {mobileView === 'list' && (
        <div className="md:hidden flex-1 overflow-y-auto p-4 pb-[var(--safe-bottom)] bg-canvas space-y-4">
          {CONSOLE_WORKSPACES.map(w => (
            <div key={w.key}>
              <div className="text-xs font-semibold text-textMuted uppercase tracking-wider px-1 mb-1.5">{t(w.labelKey)}</div>
              <div className="space-y-1">
                {w.items.map((item) => (
                  <button
                    key={item.key}
                    onClick={() => { openItem(item.key); setMobileView('detail') }}
                    className="w-full flex items-center gap-3 px-4 py-3 rounded-card hover:bg-elevated active:bg-border/50 transition-colors text-left"
                  >
                    <div className="w-9 h-9 rounded-control bg-primary-500/10 flex items-center justify-center shrink-0">
                      <item.icon size={18} className="text-primary-400" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-textPrimary">{t(item.labelKey)}</div>
                      <div className="text-xs text-textMuted mt-0.5">{t(item.descKey)}</div>
                    </div>
                    <ChevronRight size={16} className="text-textMuted shrink-0" />
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 移动端：详情内容区 */}
      <div className={'md:hidden flex-1 overflow-y-auto p-4 pb-[var(--safe-bottom)] bg-canvas ' + (mobileView === 'list' ? 'hidden' : '')}>
        <ActiveTab />
      </div>
    </div>
  )
}
