import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useT } from '../../i18n/I18nContext'
import { PAGE_WIDTH, type Width } from './pageWidth'
import WidthHandles from './WidthHandles'
import { useWidthDrag } from '../../hooks/useWidthDrag'

/** 页面内容列宽度的 CSS 变量：拖条的定位与抓取区宽度都按它算 */
const CONTENT_W_VAR = '--page-content-w'
/** 偏好键：拖过的宽度（px），只存用户意图；档位仍是没拖过时的默认 */
const CONTENT_W_KEY = 'page_content_width'
/** 下限比最窄的档位（narrow 576）再松一点，窄屏也不至于挤成一条 */
const CONTENT_MIN = 480
/** 拖到最宽时给两侧留的余量：留得下拖条的把手，贴边也拖得回来 */
const CONTENT_EDGE_BUDGET = 96

/** 存量值无效（没有 / 不是正数）一律当「没设过」，回落档位默认 */
function readPreference(): number {
  const raw = localStorage.getItem(CONTENT_W_KEY)
  if (raw === null) return 0
  const value = Number(raw)
  return Number.isFinite(value) && value >= CONTENT_MIN ? value : 0
}

interface ContentColumnProps {
  /** 宽度档位（默认 content）；拖过的像素是临时覆盖，不改档位 */
  width?: Width
  /** 内容列附加类名（内边距、间距等） */
  className?: string
  children: ReactNode
}

/**
 * 单列页面的内容列：档位给默认宽度，两侧留白里放可拖的条（双击回档位默认）。
 *
 * 拖条是整页高的一段（留白区本来也没内容），但高光与落点线都只在指针附近显形，
 * 所以不会糊成一片；宽度经 CSS 变量下发，拖动期间不触发 React 重渲（日志页几百个块）。
 */
export default function ContentColumn({ width = 'content', className = '', children }: ContentColumnProps) {
  const t = useT()
  const hostRef = useRef<HTMLDivElement>(null)
  const colRef = useRef<HTMLDivElement>(null)
  const [pref, setPref] = useState(readPreference)

  // 内容列实测宽度 → CSS 变量：拖条据此定位，不必知道档位对应多少像素（窗口变了也自动跟上）
  useEffect(() => {
    const col = colRef.current
    const host = hostRef.current
    if (!col || !host) return
    const sync = () => host.style.setProperty(CONTENT_W_VAR, `${col.getBoundingClientRect().width}px`)
    sync()
    const observer = new ResizeObserver(sync)
    observer.observe(col)
    return () => observer.disconnect()
  }, [])

  const { dragging, ...handleProps } = useWidthDrag({
    storageKey: CONTENT_W_KEY,
    min: CONTENT_MIN,
    measure: () => colRef.current?.getBoundingClientRect().width ?? CONTENT_MIN,
    room: () => (hostRef.current?.clientWidth ?? window.innerWidth) - CONTENT_EDGE_BUDGET,
    apply: (value) => { if (colRef.current) colRef.current.style.maxWidth = `${value}px` },
    commit: setPref,
  })

  /** 双击回档位默认：清掉覆盖与存储，也清掉拖动期间直接写在元素上的样式 */
  const resetWidth = useCallback(() => {
    if (colRef.current) colRef.current.style.maxWidth = ''
    localStorage.removeItem(CONTENT_W_KEY)
    setPref(0)
  }, [])

  return (
    <div ref={hostRef} className="relative">
      <div
        ref={colRef}
        className={`mx-auto w-full min-w-0 ${PAGE_WIDTH[width]} ${className}`}
        style={pref ? { maxWidth: `${pref}px` } : undefined}
      >
        {children}
      </div>
      <WidthHandles
        varName={CONTENT_W_VAR}
        dragging={dragging}
        title={t('common:contentWidthHint')}
        onReset={resetWidth}
        {...handleProps}
      />
    </div>
  )
}
