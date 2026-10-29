import { useId } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import { useT } from '../i18n/I18nContext'
import { mainNavItems, NAV_TONES, type NavItem, type NavTone } from '../utils/navRegistry.tsx'
import { usePendingRequests } from '../hooks/usePendingRequests'

interface MobileNavProps {
  closeDrawer?: () => void
}

/**
 * 悬浮导航占掉的竖向空间：栏高 4rem（64px）+ 离屏幕底 0.75rem（12px）+ 安全区。
 *
 * 这份数值同时给两处用：栏自己的定位，以及各滚动容器底部的余量（index.css 的
 * --mobile-nav-reserved / .pb-nav，全站统一取它）。分开写必然漂移——栏改高了而
 * 余量没改，最后一屏内容就又被胶囊压住。
 *
 * 余量要挂在**真正滚动的那个容器**上（页面自带的 overflow-y-auto、PageShell 的
 * 内容列、会话列表），不能挂到外层 main：页面自己的滚动容器在 main 里，main 加
 * padding 只会把内容盒压矮，滚动区域的下沿正好停在胶囊上沿，内容永远滚不到胶囊
 * 后面，玻璃就只剩一条空底色。
 */
export const MOBILE_NAV_RESERVED = 'calc(4rem + 0.75rem + env(safe-area-inset-bottom, 0px))'

/**
 * 带渐变的线条图标。
 *
 * lucide 的图标本体是一堆 path，描边色取自根 svg 的 stroke（默认 currentColor）。
 * 把 stroke 换成 url(#渐变) 就能让线条本身走渐变——形状不变，颜色从亮到深过渡。
 * <defs> 单独放在一个零尺寸 svg 里：渐变按文档 id 引用，不要求跟图标同一个 svg。
 */
function GradientIcon({ icon: Icon, size, tone }: { icon: LucideIcon; size: number; tone: NavTone }) {
  const gradientId = 'nav-grad-' + useId().replace(/:/g, '')
  const { from, to } = NAV_TONES[tone]
  return (
    <>
      <svg width="0" height="0" aria-hidden="true" className="absolute">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={from} />
            <stop offset="100%" stopColor={to} />
          </linearGradient>
        </defs>
      </svg>
      <Icon size={size} strokeWidth={2.1} stroke={`url(#${gradientId})`} />
    </>
  )
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

  // 悬浮胶囊：栏离屏幕边缘留一圈，玻璃压在内容上，滚动内容从它两侧与下方透出来。
  // 透明度给到能看清后面内容为止（surface 只上 35%），模糊放大到 2xl 让透出来的
  // 部分化开、不至于跟图标抢注意力。选中项是一枚更亮的玻璃胶囊（glass on glass），
  // 靠图标与文字上色来表态——不给它填彩色渐变，那种高饱和药丸在深色底上像发光肿块。
  return (
    <nav
      className="md:hidden fixed left-3 right-3 z-modal"
      style={{ bottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))' }}
    >
      <div className="mx-auto max-w-md h-16 rounded-[22px] border border-black/[0.06] dark:border-white/[0.08] bg-white/70 dark:bg-white/[0.06] backdrop-blur-lg backdrop-saturate-150 shadow-[0_10px_30px_-14px_rgba(0,0,0,0.35)] px-1.5 py-1.5">
        <div className="flex items-stretch">
          {visibleItems.map((item) => {
            const active = isActive(item)
            const tone = item.tone ?? 'primary'
            const iconSize = isCompact ? 19 : 22
            return (
              <button
                key={item.path}
                onClick={() => { closeDrawer?.(); navigate(item.path) }}
                className="group flex-1 flex flex-col items-center justify-center gap-1 pt-1.5 pb-1 rounded-2xl transition-colors duration-200 min-w-0 active:bg-elevated/30"
              >
                {/* 不给选中项加容器：渐变色由图标线条自己承担，跟微信/QQ 一样只换图标颜色 */}
                <span className="relative flex items-center justify-center">
                  {active
                    ? <GradientIcon icon={item.icon} size={iconSize} tone={tone} />
                    : <item.icon size={iconSize} strokeWidth={1.9} className="text-textMuted group-hover:text-textSecondary transition-colors" />}
                  {item.path === '/list' && pendingRequests > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-rose-500 ring-2 ring-surface" />
                  )}
                </span>
                <span
                  className={`${isCompact ? 'text-[9px]' : 'text-[10px]'} truncate max-w-full px-1 transition-colors duration-200 ${
                    active ? `font-semibold ${NAV_TONES[tone].label}` : 'font-medium text-textMuted'
                  }`}
                >
                  {t(item.i18nKey)}
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </nav>
  )
}
